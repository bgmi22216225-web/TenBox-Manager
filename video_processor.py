"""
video_processor.py

Core pipeline for bot 'V' (Telethon edition):

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
5. If present: a snapshot image is obtained (fast path: Telegram's own
   thumbnail via `thumb=-1`; fallback: full video download + FFmpeg),
   and uploaded to DESTINATION_CHANNEL_ID with a caption built from
   [header] + [link] + [footer], preserving the admin's Markdown as-is.
6. Temp files are cleaned up regardless of outcome.
"""

import asyncio
import logging
import os
import re
import uuid

import aiohttp
from telethon.tl.types import MessageEntityTextUrl

import database
from config import (
    BOT_TOKEN,
    SECONDARY_BOT_USERNAME,
    DESTINATION_CHANNEL_ID,
    SECONDARY_BOT_TIMEOUT,
    LINK_FILTER_KEYWORD,
    TEMP_DIR,
)

logger = logging.getLogger("V.video_processor")

_URL_RE = re.compile(r"https?://\S+")
_TELEGRAM_API_BASE = "https://api.telegram.org"

# Strict 1-to-1 mapping state.
_job_queue: asyncio.Queue = asyncio.Queue()
_pending_future: asyncio.Future | None = None
_worker_task: asyncio.Task | None = None


async def _post_snapshot_via_bot_api(photo_path: str, caption: str) -> None:
    """
    Posts the final image+caption straight through Telegram's plain HTTP
    Bot API instead of the Telethon (MTProto) client. This sidesteps
    Telethon's peer/entity resolution entirely: bot accounts aren't even
    allowed to call GetDialogsRequest over MTProto (BotMethodInvalidError),
    but the plain Bot API's sendPhoto works with a bare chat_id as long
    as the bot is an admin of that chat — no pre-resolved entity needed.
    """
    url = f"{_TELEGRAM_API_BASE}/bot{BOT_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as f:
        data = aiohttp.FormData()
        data.add_field("chat_id", str(DESTINATION_CHANNEL_ID))
        if caption:
            data.add_field("caption", caption)
            data.add_field("parse_mode", "Markdown")
        data.add_field("photo", f, filename=os.path.basename(photo_path))
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data) as resp:
                result = await resp.json()

    if not result.get("ok"):
        raise RuntimeError(f"Telegram Bot API sendPhoto failed: {result}")


class Job:
    __slots__ = ("id", "message")

    def __init__(self, message):
        self.id = uuid.uuid4().hex
        self.message = message


def enqueue_video(message) -> None:
    """Called by the source-channel handler for every incoming video."""
    job = Job(message)
    _job_queue.put_nowait(job)
    logger.info(f"Job {job.id} queued (source msg_id={message.id}). Queue size={_job_queue.qsize()}")


def resolve_secondary_reply(message) -> bool:
    """
    Called by the handler listening on the secondary bot's chat (both new
    messages AND edits — many bots edit their "Uploading..." status
    message into the final result instead of sending a new one).

    Only resolves the pending future if the message actually LOOKS like
    the final result (has a photo/video attached, or contains a link).
    Pure status/progress text (e.g. "Uploading video...") is ignored so
    the worker keeps waiting for the real reply instead of giving up early.
    """
    global _pending_future
    if _pending_future is None or _pending_future.done():
        return False

    if not _looks_like_final_reply(message):
        preview = (message.raw_text or "")[:80]
        logger.info(f"Ignoring intermediate status message from secondary bot: {preview!r}")
        return False

    _pending_future.set_result(message)
    return True


def _looks_like_final_reply(message) -> bool:
    """A message is treated as final if it carries media, or a link."""
    if message.photo or message.video or message.document:
        return True
    return _extract_tenbox_link(message) is not None


def _extract_tenbox_link(message) -> str | None:
    """
    Extracts a link containing LINK_FILTER_KEYWORD from the reply. Handles
    BOTH real-world formats seen from link-generator bots:
      1. Plain visible URL text (auto-detected, same text as the URL).
      2. A hidden hyperlink behind different display text, e.g. a
         "📥 Download" button (a text-link entity, real URL not visible).
    """
    text = message.raw_text or ""
    candidates = list(_URL_RE.findall(text))

    for entity, _entity_text in (message.get_entities_text() or []):
        if isinstance(entity, MessageEntityTextUrl):
            candidates.append(entity.url)

    for url in candidates:
        if LINK_FILTER_KEYWORD in url.lower():
            return url

    if candidates:
        logger.info(f"Reply had link(s) but none matched '{LINK_FILTER_KEYWORD}': {candidates}")
    else:
        logger.info(f"Reply had no extractable link at all. Raw text: {text!r}")
    return None


def _build_caption(header: str, link: str, footer: str) -> str:
    parts = [p for p in (header.strip(), link.strip(), footer.strip()) if p]
    return "\n\n".join(parts)


def _safe_remove(*paths: str) -> None:
    """Best-effort cleanup of temp files."""
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError as e:
            logger.warning(f"Could not remove temp file {path}: {e}")


async def _extract_ffmpeg_snapshot(video_path: str, job_id: str, timestamp: str = "00:00:02") -> str:
    """Fallback: extract a single JPEG frame from a downloaded video using FFmpeg."""
    output_path = os.path.join(TEMP_DIR, f"snap_{job_id}.jpg")
    cmd = [
        "ffmpeg", "-y",
        "-ss", timestamp,
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", "2",
        output_path,
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0 or not os.path.exists(output_path):
        error_msg = stderr.decode(errors="ignore")[-500:]
        raise RuntimeError(f"FFmpeg snapshot extraction failed: {error_msg}")
    logger.info(f"Snapshot extracted via FFmpeg: {output_path}")
    return output_path


async def _get_snapshot_fast(client: TelegramClient, message, job_id: str) -> str:
    """
    Fast path: Telethon can download just a video's existing thumbnail
    (a few KB, already a first-frame-style preview) by passing thumb=-1
    to download_media, instead of the whole video. Falls back to
    downloading the full video + FFmpeg only if no thumbnail exists.
    """
    thumb_path = os.path.join(TEMP_DIR, f"thumb_{job_id}.jpg")
    result = await client.download_media(message, file=thumb_path, thumb=-1)
    if result:
        logger.info(f"Job {job_id}: used Telegram's built-in thumbnail (fast path, no full download).")
        return result

    logger.info(f"Job {job_id}: no thumbnail available — falling back to full video download + FFmpeg.")
    video_path = os.path.join(TEMP_DIR, f"vid_{job_id}.mp4")
    downloaded_path = await client.download_media(message, file=video_path)
    try:
        return await _extract_ffmpeg_snapshot(downloaded_path, job_id)
    finally:
        _safe_remove(downloaded_path)


async def _process_job(client: TelegramClient, job: Job) -> None:
    global _pending_future
    message = job.message
    snapshot_path = None

    try:
        # Step 1: send the video to the secondary bot and await its reply.
        _pending_future = asyncio.get_event_loop().create_future()

        sent = await client.send_file(
            SECONDARY_BOT_USERNAME,
            file=message.video,
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

        # Step 3: get the snapshot image, based on what the secondary bot sent back.
        #   - reply has a photo  -> use it directly
        #   - reply has a video  -> use its thumbnail (fallback: download + ffmpeg)
        #   - reply has neither  -> use ORIGINAL source video's thumbnail (fallback: same)
        if reply.photo:
            snapshot_path = await client.download_media(
                reply, file=os.path.join(TEMP_DIR, f"img_{job.id}.jpg")
            )
            logger.info(f"Job {job.id}: reply was an image — using it directly as the snapshot.")
        elif reply.video:
            snapshot_path = await _get_snapshot_fast(client, reply, job.id)
        else:
            snapshot_path = await _get_snapshot_fast(client, message, job.id)

        # Step 4: build caption with dynamic header/footer, preserving admin's markdown as-is.
        header, footer = await database.get_format()
        caption = _build_caption(header, link, footer)

        await _post_snapshot_via_bot_api(snapshot_path, caption)
        logger.info(f"Job {job.id}: snapshot posted to destination channel.")

    except Exception:
        logger.exception(f"Job {job.id}: unhandled error while processing.")
    finally:
        _safe_remove(snapshot_path)


async def _worker(client: TelegramClient) -> None:
    logger.info("Video processing worker started.")
    while True:
        job: Job = await _job_queue.get()
        try:
            await _process_job(client, job)
        finally:
            _job_queue.task_done()


def start_worker(client: TelegramClient) -> None:
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
