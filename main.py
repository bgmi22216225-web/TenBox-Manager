"""
main.py

Production Telegram automation bot (Pyrogram) for Railway.app.

Architecture
------------
Two Pyrogram clients are used because a bot account cannot reliably
initiate/receive DMs from another bot via the Bot API in all cases,
and because monitoring an arbitrary source channel generally requires
a user session with access to that channel:

  * `user_client` - a Userbot session (SESSION_STRING) that:
      - listens to SOURCE_CHANNEL_ID for incoming videos
      - forwards each video to TARGET_BOT_USERNAME
      - listens for TARGET_BOT_USERNAME's reply (video + link)

  * `bot_client` - a normal Bot (BOT_TOKEN) that:
      - handles all admin commands
      - posts the final snapshot + caption to DESTINATION_CHANNEL_ID

A single `asyncio.Queue` feeds a lone worker task, so videos are
processed strictly one at a time -- this guarantees 1:1 correlation
between an outgoing video and the target bot's reply without needing
any additional message-id bookkeeping.
"""

import asyncio
import logging
import os
import re
import shutil
import sys
import time
import uuid
from typing import Optional

import ffmpeg
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait, RPCError

import database as db

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("main")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        logger.critical("Missing required environment variable: %s", name)
        sys.exit(1)
    return value


API_ID = int(_require_env("API_ID"))
API_HASH = _require_env("API_HASH")
BOT_TOKEN = _require_env("BOT_TOKEN")
SESSION_STRING = _require_env("SESSION_STRING")
TARGET_BOT_USERNAME = _require_env("TARGET_BOT_USERNAME").lstrip("@")
SOURCE_CHANNEL_ID = int(_require_env("SOURCE_CHANNEL_ID"))
DESTINATION_CHANNEL_ID = int(_require_env("DESTINATION_CHANNEL_ID"))
DATABASE_URL = _require_env("DATABASE_URL")
SUPER_ADMIN_ID = int(_require_env("SUPER_ADMIN_ID"))

TARGET_REPLY_TIMEOUT = int(os.environ.get("TARGET_REPLY_TIMEOUT", "180"))
TEMP_DIR = os.environ.get("TEMP_DIR", "/tmp/bot_workdir")
LINK_REGEX = re.compile(r"https?://\S+")

os.makedirs(TEMP_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

user_client = Client(
    name="userbot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    in_memory=True,
)

bot_client = Client(
    name="bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)

# ---------------------------------------------------------------------------
# Queue + response correlation state
# ---------------------------------------------------------------------------

video_queue: "asyncio.Queue[Message]" = asyncio.Queue()

# Only one outbound request is ever in flight at a time (the worker awaits
# a reply before dequeuing the next item), so a single pending future is
# sufficient to correlate the target bot's reply with the request that
# triggered it.
_pending_reply: Optional[asyncio.Future] = None
_pending_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Admin authorization helpers
# ---------------------------------------------------------------------------

def admin_only(func):
    async def wrapper(client: Client, message: Message):
        try:
            if not await db.is_admin(message.from_user.id):
                await message.reply_text("You are not authorized to use this command.")
                return
        except Exception:
            logger.exception("Admin check failed")
            await message.reply_text("Internal error checking permissions. Try again shortly.")
            return
        await func(client, message)
    return wrapper


def super_admin_only(func):
    async def wrapper(client: Client, message: Message):
        if message.from_user.id != SUPER_ADMIN_ID:
            await message.reply_text("Only the super admin can use this command.")
            return
        await func(client, message)
    return wrapper


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def extract_link(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    match = LINK_REGEX.search(text)
    return match.group(0) if match else None


def compose_caption(header: Optional[str], link: str, footer: Optional[str]) -> str:
    parts = []
    if header:
        parts.append(header.strip())
    parts.append(link.strip())
    if footer:
        parts.append(footer.strip())
    return "\n\n".join(parts)


def generate_thumbnail(video_path: str, output_path: str, timestamp: float = 1.0) -> None:
    """Extract a single high-quality frame from the video using ffmpeg."""
    try:
        (
            ffmpeg
            .input(video_path, ss=timestamp)
            .output(output_path, vframes=1, qscale=2)
            .overwrite_output()
            .run(quiet=True, capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as e:
        stderr = e.stderr.decode(errors="ignore") if e.stderr else str(e)
        raise RuntimeError(f"ffmpeg thumbnail generation failed: {stderr}") from e

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("ffmpeg produced no thumbnail output")


def cleanup_files(*paths: str) -> None:
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            logger.warning("Failed to remove temp file: %s", path, exc_info=True)


def new_work_dir() -> str:
    path = os.path.join(TEMP_DIR, uuid.uuid4().hex)
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

async def send_to_target_and_wait(video_message: Message) -> Message:
    """Forward a video to the target bot and wait for its reply."""
    global _pending_reply

    async with _pending_lock:
        loop = asyncio.get_event_loop()
        _pending_reply = loop.create_future()
        try:
            await video_message.forward(TARGET_BOT_USERNAME)
        except FloodWait as e:
            logger.warning("FloodWait while forwarding, sleeping %s seconds", e.value)
            await asyncio.sleep(e.value)
            await video_message.forward(TARGET_BOT_USERNAME)

        try:
            reply = await asyncio.wait_for(_pending_reply, timeout=TARGET_REPLY_TIMEOUT)
        finally:
            _pending_reply = None

    return reply


async def process_video(video_message: Message) -> None:
    work_dir = new_work_dir()
    downloaded_video_path = None
    thumbnail_path = None

    try:
        reply = await send_to_target_and_wait(video_message)

        link = extract_link(reply.text or reply.caption)
        if not link:
            logger.error("No link found in target bot reply (chat message id %s)", reply.id)
            return

        if not reply.video and not reply.document:
            logger.error("Target bot reply did not contain a video (message id %s)", reply.id)
            return

        downloaded_video_path = await reply.download(file_name=os.path.join(work_dir, "source.mp4"))

        thumbnail_path = os.path.join(work_dir, "thumb.jpg")
        await asyncio.to_thread(generate_thumbnail, downloaded_video_path, thumbnail_path)

        header = await db.get_header()
        footer = await db.get_footer()
        caption = compose_caption(header, link, footer)

        await bot_client.send_photo(
            chat_id=DESTINATION_CHANNEL_ID,
            photo=thumbnail_path,
            caption=caption,
        )
        logger.info("Posted snapshot for video message %s to destination channel", video_message.id)

    except asyncio.TimeoutError:
        logger.error(
            "Timed out after %ss waiting for %s to reply to video %s",
            TARGET_REPLY_TIMEOUT, TARGET_BOT_USERNAME, video_message.id,
        )
    except RPCError:
        logger.exception("Telegram RPC error while processing video %s", video_message.id)
    except Exception:
        logger.exception("Unexpected error while processing video %s", video_message.id)
    finally:
        cleanup_files(downloaded_video_path, thumbnail_path)
        shutil.rmtree(work_dir, ignore_errors=True)


async def queue_worker() -> None:
    logger.info("Queue worker started.")
    while True:
        video_message = await video_queue.get()
        try:
            await process_video(video_message)
        except Exception:
            logger.exception("Worker loop caught an unhandled exception")
        finally:
            video_queue.task_done()


# ---------------------------------------------------------------------------
# Userbot handlers: source channel monitor + target bot reply listener
# ---------------------------------------------------------------------------

@user_client.on_raw_update()
async def on_userbot_raw_update(client: Client, update, users, chats):
    # TEMPORARY: fires on every single raw update the userbot session
    # receives from Telegram, with zero filtering. If this never prints
    # anything, updates aren't reaching the process at all (network,
    # account, or session issue) - it's not a filter/handler bug.
    logger.info("DEBUG userbot raw update: %s", type(update).__name__)


@user_client.on_message(filters.chat(SOURCE_CHANNEL_ID))
async def on_source_any_debug(client: Client, message: Message):
    # TEMPORARY: logs every message seen in the source channel, regardless
    # of type, to diagnose whether updates are arriving at all and what
    # media type they carry. Safe to remove once videos are confirmed
    # flowing through the queue.
    logger.info(
        "DEBUG source-channel message id=%s media=%s has_video=%s has_document=%s has_animation=%s",
        message.id, message.media, bool(message.video), bool(message.document), bool(message.animation),
    )


@user_client.on_message(filters.chat(SOURCE_CHANNEL_ID) & filters.video)
async def on_source_video(client: Client, message: Message):
    logger.info("Queued new video from source channel (message id %s)", message.id)
    await video_queue.put(message)


@user_client.on_message(filters.private & filters.user(TARGET_BOT_USERNAME))
async def on_target_bot_reply(client: Client, message: Message):
    global _pending_reply
    if _pending_reply is not None and not _pending_reply.done():
        _pending_reply.set_result(message)
    else:
        logger.warning("Received unsolicited message from target bot; ignoring.")


# ---------------------------------------------------------------------------
# Admin command handlers (bot_client)
# ---------------------------------------------------------------------------

@bot_client.on_raw_update()
async def on_bot_raw_update(client: Client, update, users, chats):
    # TEMPORARY: same diagnostic as the userbot's raw handler, but for
    # the bot session (covers /start and other commands not firing).
    logger.info("DEBUG bot raw update: %s", type(update).__name__)


@bot_client.on_message(filters.command("start"))
async def cmd_start(client: Client, message: Message):
    await message.reply_text(
        "Bot is running.\n\n"
        "Use /viewtemplate to see the current header/footer, "
        "or /admins to list authorized admins."
    )


@bot_client.on_message(filters.command("setheader"))
@admin_only
async def cmd_setheader(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /setheader <text>")
        return
    text = message.text.split(None, 1)[1]
    await db.set_template(db.TEMPLATE_HEADER_KEY, text)
    await message.reply_text("Header updated.")


@bot_client.on_message(filters.command("setfooter"))
@admin_only
async def cmd_setfooter(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /setfooter <text>")
        return
    text = message.text.split(None, 1)[1]
    await db.set_template(db.TEMPLATE_FOOTER_KEY, text)
    await message.reply_text("Footer updated.")


@bot_client.on_message(filters.command("delheader"))
@admin_only
async def cmd_delheader(client: Client, message: Message):
    removed = await db.delete_template(db.TEMPLATE_HEADER_KEY)
    await message.reply_text("Header removed." if removed else "No header was set.")


@bot_client.on_message(filters.command("delfooter"))
@admin_only
async def cmd_delfooter(client: Client, message: Message):
    removed = await db.delete_template(db.TEMPLATE_FOOTER_KEY)
    await message.reply_text("Footer removed." if removed else "No footer was set.")


@bot_client.on_message(filters.command("viewtemplate"))
@admin_only
async def cmd_viewtemplate(client: Client, message: Message):
    header = await db.get_header()
    footer = await db.get_footer()
    preview = compose_caption(header, "{LINK}", footer)
    text = (
        f"**Header:**\n{header or '_not set_'}\n\n"
        f"**Footer:**\n{footer or '_not set_'}\n\n"
        f"**Preview:**\n{preview}"
    )
    await message.reply_text(text)


@bot_client.on_message(filters.command("addadmin"))
@super_admin_only
async def cmd_addadmin(client: Client, message: Message):
    if len(message.command) < 2 or not message.command[1].isdigit():
        await message.reply_text("Usage: /addadmin <user_id>")
        return
    new_admin_id = int(message.command[1])
    added = await db.add_admin(new_admin_id, added_by=message.from_user.id)
    await message.reply_text(
        f"Added `{new_admin_id}` as admin." if added else f"`{new_admin_id}` is already an admin."
    )


@bot_client.on_message(filters.command("deladmin"))
@super_admin_only
async def cmd_deladmin(client: Client, message: Message):
    if len(message.command) < 2 or not message.command[1].isdigit():
        await message.reply_text("Usage: /deladmin <user_id>")
        return
    target_id = int(message.command[1])
    if target_id == SUPER_ADMIN_ID:
        await message.reply_text("Cannot remove the super admin.")
        return
    removed = await db.remove_admin(target_id)
    await message.reply_text(
        f"Removed `{target_id}` from admins." if removed else f"`{target_id}` was not an admin."
    )


@bot_client.on_message(filters.command("admins"))
@admin_only
async def cmd_admins(client: Client, message: Message):
    admin_ids = await db.list_admins()
    lines = [f"- `{uid}`" + (" (super admin)" if uid == SUPER_ADMIN_ID else "") for uid in admin_ids]
    await message.reply_text("**Authorized admins:**\n" + "\n".join(lines))


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

async def start_client_with_retry(client: Client, label: str, retries: int = 5) -> None:
    delay = 5
    for attempt in range(1, retries + 1):
        try:
            await client.start()
            logger.info("%s client started.", label)
            return
        except Exception:
            logger.exception("%s client failed to start (attempt %s/%s)", label, attempt, retries)
            if attempt == retries:
                raise
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


async def main() -> None:
    logger.info("Initializing database...")
    await db.init_db(DATABASE_URL, SUPER_ADMIN_ID)

    await start_client_with_retry(user_client, "Userbot")
    await start_client_with_retry(bot_client, "Bot")

    # The userbot runs with an in-memory session, so its peer cache
    # (access hashes for chats/channels) is empty on every restart.
    # Without resolving peers first, incoming updates for channels the
    # client hasn't "seen" yet are silently dropped by Pyrogram - no
    # error, the handler just never fires. Iterating dialogs populates
    # the cache for every chat the account is a member of, including
    # SOURCE_CHANNEL_ID.
    try:
        dialog_count = 0
        async for _ in user_client.get_dialogs():
            dialog_count += 1
        logger.info("Synced %s dialogs into userbot peer cache.", dialog_count)
    except Exception:
        logger.exception("Failed to sync dialogs - source channel updates may not be received.")

    try:
        source_chat = await user_client.get_chat(SOURCE_CHANNEL_ID)
        logger.info("Resolved source channel: %s", source_chat.title or source_chat.id)
    except Exception:
        logger.exception(
            "Could not resolve SOURCE_CHANNEL_ID (%s). Confirm the userbot account is a "
            "member and the ID is correct.", SOURCE_CHANNEL_ID,
        )

    worker_task = asyncio.create_task(queue_worker())

    logger.info("Bot fully started. Listening for videos in source channel...")

    try:
        await asyncio.Event().wait()  # run forever
    finally:
        worker_task.cancel()
        await db.close_db()
        await user_client.stop()
        await bot_client.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt).")
