"""
handlers/video_processor.py

Core pipeline for bot 'V':

1. A new video appears in SOURCE_CHANNEL_ID          -> enqueued as a Job.
2. A single worker coroutine drains the queue FIFO, ONE JOB AT A TIME.
   Because only one job is ever "in flight" toward the secondary bot,
   there is no possibility of one video's link being swapped with
   another's — the queue itself is the race-condition guard.
3. The job's video is sent to SECONDARY_BOT_USERNAME. The worker then
   awaits a dedicated asyncio.Future that is resolved by the message
   handler listening on that chat (see `resolve_secondary_reply`).
4. The reply text is scanned for a link containing the filter keyword
   ("tenbox" by default, case-insensitive). If it is absent, the job is
   dropped — nothing is posted.
5. If present: the ORIGINAL video is downloaded (never forwarded), a
   single-frame JPEG snapshot is extracted via FFmpeg, and that snapshot
   is uploaded to DESTINATION_CHANNEL_ID with a caption built from
   [header] + [link] + [footer], preserving the admin's Markdown as-is.
6. Temp files are cleaned up regardless of outcome.
"""

import asyncio
import logging
import os
import re
import uuid

from pyrogram import Client
from pyrogram.types import Message
from pyrogram.enums import ParseMode

import database
from config import (
    SECONDARY_BOT_USERNAME,
    DESTINATION_CHANNEL_ID,
    SECONDARY_BOT_TIMEOUT,
    LINK_FILTER_KEYWORD,
    TEMP_DIR,
)
from utils.snapshot import extract_snapshot, safe_remove

logger = logging.getLogger("V.video_processor")

_URL_RE = re.compile(r"https?://\S+")

# Strict 1-to-1 mapping state.
_job_queue: asyncio.Queue = asyncio.Queue()
_current_job_id: str | None = None
_pending_future: asyncio.Future | None = None
_worker_task: asyncio.Task | None = None


class Job:
    __slots__ = ("id", "message")

    def __init__(self, message: Message):
        self.id = uuid.uuid4().hex
        self.message = message


def enqueue_video(message: Message) -> None:
    """Called by the source-channel handler for every incoming video."""
    job = Job(message)
    _job_queue.put_nowait(job)
    logger.info(f"Job {job.id} queued (source msg_id={message.id}). Queue size={_job_queue.qsize()}")


def resolve_secondary_reply(message: Message) -> bool:
    """
    Called by the handler listening on the secondary bot's chat.
    Resolves the pending future IF one is currently awaited.
    Returns True if the message was consumed as the awaited reply.
    """
    global _pending_future
    if _pending_future is not None and not _pending_future.done():
        _pending_future.set_result(message)
        return True
    return False


def _extract_tenbox_link(message: Message) -> str | None:
    """
    Checks ONLY hyperlink entities (a link attached to text, e.g. a
    "📥 Download" button/word) — NOT the plain caption/text itself.
    A plain URL typed directly in the caption is ignored on purpose.
    """
    text = message.text or message.caption or ""
    entities = list(message.entities or []) + list(message.caption_entities or [])

    candidates = []
    for entity in entities:
        entity_type = str(entity.type.name if hasattr(entity.type, "name") else entity.type).upper()
        if entity_type == "TEXT_LINK" and getattr(entity, "url", None):
            candidates.append(entity.url)

    for url in candidates:
        if LINK_FILTER_KEYWORD in url.lower():
            return url

    if candidates:
        logger.info(f"Reply had hyperlink(s) but none matched '{LINK_FILTER_KEYWORD}': {candidates}")
    else:
        logger.info(f"Reply had no hyperlink entity at all. Raw text: {text!r}")
    return None


def _build_caption(header: str, link: str, footer: str) -> str:
    parts = [p for p in (header.strip(), link.strip(), footer.strip()) if p]
    return "\n\n".join(parts)


async def _process_job(client: Client, job: Job) -> None:
    global _pending_future
    message = job.message
    video_path = None
    snapshot_path = None

    try:
        # Step 1: send the video to the secondary bot and await its reply.
        _pending_future = asyncio.get_event_loop().create_future()

        sent = await client.send_video(
            chat_id=SECONDARY_BOT_USERNAME,
            video=message.video.file_id,
            caption=f"job:{job.id}",
        )
        logger.info(f"Job {job.id}: video forwarded to {SECONDARY_BOT_USERNAME} (sent msg_id={sent.id})")

        try:
            reply = await asyncio.wait_for(_pending_future, timeout=SECONDARY_BOT_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(f"Job {job.id}: timed out waiting for secondary bot reply. Skipping.")
            return
        finally:
            _pending_future = None

        # Step 2: filter — link must strictly contain the keyword (e.g. 'tenbox').
        link = _extract_tenbox_link(reply)
        if not link:
            logger.info(f"Job {job.id}: reply had no '{LINK_FILTER_KEYWORD}' link. Dropping (no post).")
            return

        logger.info(f"Job {job.id}: matched link -> {link}")

        # Step 3: download original video (never forwarded to destination) + snapshot.
        video_path = await client.download_media(
            message, file_name=os.path.join(TEMP_DIR, f"vid_{job.id}.mp4")
        )
        snapshot_path = await extract_snapshot(video_path, TEMP_DIR)

        # Step 4: build caption with dynamic header/footer, preserving admin's markdown as-is.
        header, footer = await database.get_format()
        caption = _build_caption(header, link, footer)

        await client.send_photo(
            chat_id=DESTINATION_CHANNEL_ID,
            photo=snapshot_path,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.info(f"Job {job.id}: snapshot posted to destination channel.")

    except Exception:
        logger.exception(f"Job {job.id}: unhandled error while processing.")
    finally:
        safe_remove(video_path, snapshot_path)


async def _worker(client: Client) -> None:
    global _current_job_id
    logger.info("Video processing worker started.")
    while True:
        job: Job = await _job_queue.get()
        _current_job_id = job.id
        try:
            await _process_job(client, job)
        finally:
            _current_job_id = None
            _job_queue.task_done()


def start_worker(client: Client) -> None:
    """Call once at startup (after client.start())."""
    global _worker_task
    if _worker_task is None:
        _worker_task = asyncio.get_event_loop().create_task(_worker(client))
        logger.info("Worker task scheduled.")


def stop_worker() -> None:
    global _worker_task
    if _worker_task is not None:
        _worker_task.cancel()
        _worker_task = None
