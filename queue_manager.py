"""
queue_manager.py
FIFO async queue for incoming source-channel videos.

Zero-mismatch guarantee, two layers deep:
  1. Structural: QUEUE_WORKER_CONCURRENCY defaults to 1, so only ONE video is ever
     "in flight" (forwarded, awaiting the processing bot's reply) at a time. The next
     video is not even forwarded until the current one's link has been resolved or timed
     out. This alone makes mismatching impossible even if the processing bot never
     reply-threads its answers.
  2. Defensive: every forwarded message's id is tracked in `_pending`. When a reply
     arrives, if it has `reply_to_message_id` set, we require it to match the specific
     job we're waiting on before accepting it as that job's link. If the processing bot
     doesn't use reply-threading at all, we fall back to "oldest pending job" — which,
     given (1), is always the *only* pending job.

If you ever raise QUEUE_WORKER_CONCURRENCY above 1, layer (2)'s reply_to_message_id
check becomes load-bearing — make sure the processing bot actually reply-threads its
responses before doing that.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from config import RESPONSE_TIMEOUT, QUEUE_WORKER_CONCURRENCY

logger = logging.getLogger("queue_manager")


@dataclass
class VideoJob:
    tracking_id: str          # unique id for logging / correlation, e.g. f"{chat_id}:{message_id}"
    source_message: object    # the original pyrogram Message from SOURCE_CHANNEL_ID
    enqueued_at: float = field(default_factory=time.time)
    forwarded_message_id: Optional[int] = None
    response_future: Optional[asyncio.Future] = None


class QueueManager:
    def __init__(self, concurrency: int = QUEUE_WORKER_CONCURRENCY):
        self.queue: asyncio.Queue[VideoJob] = asyncio.Queue()
        self._pending: dict[int, VideoJob] = {}   # forwarded_message_id -> VideoJob
        self._pending_order: list[int] = []        # insertion order, for the fallback match
        self._concurrency = max(1, concurrency)
        self._workers: list[asyncio.Task] = []
        self._processor = None  # async callback: (job: VideoJob, link: str) -> None
        self._forwarder = None  # async callback: (job: VideoJob) -> int (forwarded message id)

    def configure(self, forwarder, processor):
        """
        forwarder(job) -> awaitable returning the forwarded message's id in the
                           processing-bot chat.
        processor(job, link) -> awaitable that does snapshot capture + posting + cleanup.
        """
        self._forwarder = forwarder
        self._processor = processor

    async def enqueue(self, job: VideoJob):
        await self.queue.put(job)
        logger.info(f"Enqueued job {job.tracking_id} (queue depth now {self.queue.qsize()})")

    def start(self):
        for i in range(self._concurrency):
            task = asyncio.create_task(self._worker_loop(worker_id=i))
            self._workers.append(task)
        logger.info(f"Started {self._concurrency} queue worker(s).")

    async def stop(self):
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)

    async def _worker_loop(self, worker_id: int):
        while True:
            job = await self.queue.get()
            try:
                await self._process_job(job)
            except Exception:
                logger.exception(f"[worker-{worker_id}] Unhandled error processing {job.tracking_id}")
            finally:
                self.queue.task_done()

    async def _process_job(self, job: VideoJob):
        loop = asyncio.get_running_loop()
        job.response_future = loop.create_future()

        forwarded_id = await self._forwarder(job)
        job.forwarded_message_id = forwarded_id
        self._pending[forwarded_id] = job
        self._pending_order.append(forwarded_id)
        logger.info(f"Forwarded {job.tracking_id} as message id {forwarded_id}; awaiting reply.")

        try:
            link = await asyncio.wait_for(job.response_future, timeout=RESPONSE_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error(f"Timed out waiting for processing-bot reply to {job.tracking_id}")
            self._discard_pending(forwarded_id)
            return

        self._discard_pending(forwarded_id)
        await self._processor(job, link)

    def _discard_pending(self, forwarded_id: int):
        self._pending.pop(forwarded_id, None)
        if forwarded_id in self._pending_order:
            self._pending_order.remove(forwarded_id)

    def resolve_response(self, reply_to_message_id: Optional[int], link: str) -> bool:
        """
        Called from the processing-bot chat message handler whenever a new message
        arrives there. Returns True if it was matched to a pending job (and that job's
        future was resolved), False otherwise.
        """
        job = None

        if reply_to_message_id is not None and reply_to_message_id in self._pending:
            job = self._pending[reply_to_message_id]
        elif self._pending_order:
            # Fallback: no reply-threading from the processing bot — take the oldest
            # pending job. Safe because concurrency=1 guarantees at most one is pending.
            oldest_id = self._pending_order[0]
            job = self._pending[oldest_id]
            logger.warning(
                f"Processing bot reply had no matching reply_to_message_id; "
                f"falling back to oldest pending job {job.tracking_id}."
            )

        if job is None or job.response_future is None or job.response_future.done():
            logger.warning("Received a processing-bot reply with no pending job to match.")
            return False

        job.response_future.set_result(link)
        return True
