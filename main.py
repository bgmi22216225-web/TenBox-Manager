"""
main.py
Entry point. Wires together:
  - `bot`  : Bot API client (BOT_TOKEN) -> admin commands + posts to DESTINATION_CHANNEL_ID
  - `user` : Userbot client (SESSION_STRING) -> reads SOURCE_CHANNEL_ID, forwards videos
             to PROCESSING_BOT_USERNAME, and reads that bot's replies
  - QueueManager -> strict FIFO video processing with zero-mismatch link mapping
"""

import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand

import config
import database as db
import media
from queue_manager import QueueManager, VideoJob

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

bot = Client(
    "bot_session",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    workdir="sessions",
)

user = Client(
    "user_session",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    session_string=config.SESSION_STRING,
    workdir="sessions",
)

qm = QueueManager()

ADMIN_COMMANDS = [
    BotCommand("header", "Set the post header (main admin only)"),
    BotCommand("footer", "Set the post footer (main admin only)"),
    BotCommand("addadmin", "Add a new admin (main admin only)"),
    BotCommand("deladmin", "Remove an admin (main admin only)"),
    BotCommand("status", "Show bot / queue status"),
    BotCommand("stats", "Show admin list and settings summary"),
]


# ----------------------------------------------------------------------------
# Admin commands (bot client)
# ----------------------------------------------------------------------------

@bot.on_message(filters.command("header") & filters.private)
async def cmd_header(client: Client, message: Message):
    if not db.is_main_admin(message.from_user.id):
        await message.reply_text("Only the main admin can set the header.")
        return
    text = message.text.split(None, 1)
    if len(text) < 2:
        await message.reply_text("Usage: /header <text>")
        return
    await db.set_header(text[1])
    await message.reply_text("Header updated.")


@bot.on_message(filters.command("footer") & filters.private)
async def cmd_footer(client: Client, message: Message):
    if not db.is_main_admin(message.from_user.id):
        await message.reply_text("Only the main admin can set the footer.")
        return
    text = message.text.split(None, 1)
    if len(text) < 2:
        await message.reply_text("Usage: /footer <text>")
        return
    await db.set_footer(text[1])
    await message.reply_text("Footer updated.")


@bot.on_message(filters.command("addadmin") & filters.private)
async def cmd_addadmin(client: Client, message: Message):
    if not db.is_main_admin(message.from_user.id):
        await message.reply_text("Only the main admin can add admins.")
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.reply_text("Usage: /addadmin <user_id>")
        return
    new_id = int(parts[1])
    added = await db.add_admin(new_id, added_by=message.from_user.id)
    await message.reply_text(f"Added admin {new_id}." if added else f"{new_id} is already an admin.")


@bot.on_message(filters.command("deladmin") & filters.private)
async def cmd_deladmin(client: Client, message: Message):
    if not db.is_main_admin(message.from_user.id):
        await message.reply_text("Only the main admin can remove admins.")
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.reply_text("Usage: /deladmin <user_id>")
        return
    target_id = int(parts[1])
    removed = await db.remove_admin(target_id)
    await message.reply_text(f"Removed admin {target_id}." if removed else f"{target_id} was not an admin.")


@bot.on_message(filters.command("status") & filters.private)
async def cmd_status(client: Client, message: Message):
    if not await db.is_admin(message.from_user.id):
        await message.reply_text("You are not authorized to use this bot.")
        return
    await message.reply_text(
        f"Queue depth: {qm.queue.qsize()}\n"
        f"Pending (awaiting processing-bot reply): {len(qm._pending)}"
    )


@bot.on_message(filters.command("stats") & filters.private)
async def cmd_stats(client: Client, message: Message):
    if not await db.is_admin(message.from_user.id):
        await message.reply_text("You are not authorized to use this bot.")
        return
    admins = await db.list_admins()
    header = await db.get_header()
    footer = await db.get_footer()
    admin_lines = "\n".join(f"- {a['user_id']} (added by {a['added_by']})" for a in admins) or "(none)"
    await message.reply_text(
        f"Main admin: {config.MAIN_ADMIN_ID}\n"
        f"Additional admins:\n{admin_lines}\n\n"
        f"Header set: {'yes' if header else 'no'}\n"
        f"Footer set: {'yes' if footer else 'no'}"
    )


# ----------------------------------------------------------------------------
# Source channel intake (userbot client)
# ----------------------------------------------------------------------------

@user.on_message(filters.chat(config.SOURCE_CHANNEL_ID) & (filters.video | filters.document))
async def on_source_video(client: Client, message: Message):
    tracking_id = f"{message.chat.id}:{message.id}"
    job = VideoJob(tracking_id=tracking_id, source_message=message)
    await qm.enqueue(job)


@user.on_message(filters.chat(config.PROCESSING_BOT_USERNAME) & filters.text & filters.incoming)
async def on_processing_bot_reply(client: Client, message: Message):
    link = media.extract_first_url(message.text)
    if not link:
        logger.warning(f"Processing bot message had no extractable URL: {message.text!r}")
        return
    reply_to = message.reply_to_message_id
    matched = qm.resolve_response(reply_to, link)
    if not matched:
        logger.warning("Processing bot reply could not be matched to any pending job.")


# ----------------------------------------------------------------------------
# Queue callbacks
# ----------------------------------------------------------------------------

async def forward_to_processing_bot(job: VideoJob) -> int:
    forwarded = await user.forward_messages(
        chat_id=config.PROCESSING_BOT_USERNAME,
        from_chat_id=job.source_message.chat.id,
        message_ids=job.source_message.id,
    )
    # forward_messages returns a Message, or a list when forwarding multiple ids
    forwarded_msg = forwarded[0] if isinstance(forwarded, list) else forwarded
    return forwarded_msg.id


async def process_video_job(job: VideoJob, link: str):
    video_path, snapshot_path = media.new_paths(prefix=job.tracking_id.replace(":", "_"))
    try:
        await job.source_message.download(file_name=video_path)

        ok = await media.capture_snapshot(video_path, snapshot_path)
        if not ok:
            logger.error(f"Skipping post for {job.tracking_id}: snapshot capture failed.")
            return

        header = await db.get_header()
        footer = await db.get_footer()
        caption_parts = [p for p in (header, link, footer) if p != ""]
        caption = "\n\n".join(caption_parts)

        await bot.send_photo(
            chat_id=config.DESTINATION_CHANNEL_ID,
            photo=snapshot_path,
            caption=caption,
        )
        logger.info(f"Posted snapshot for {job.tracking_id} to destination channel.")
    finally:
        media.cleanup_files(video_path, snapshot_path)


# ----------------------------------------------------------------------------
# Startup
# ----------------------------------------------------------------------------

async def main():
    await db.init_pool()

    qm.configure(forwarder=forward_to_processing_bot, processor=process_video_job)
    qm.start()

    await bot.start()
    await bot.set_bot_commands(ADMIN_COMMANDS)
    await user.start()

    # Pyrogram needs each chat's peer/access_hash cached in local session storage
    # before it can resolve incoming updates from that chat (channels especially).
    # Being a member isn't enough on its own — iterating dialogs forces Pyrogram
    # to fetch and cache every chat's peer info, including SOURCE_CHANNEL_ID and
    # PROCESSING_BOT_USERNAME. Without this, incoming updates fail with
    # "Peer id invalid" / "ID not found" and handlers never fire.
    logger.info("Warming up userbot peer cache...")
    dialog_count = 0
    async for _ in user.get_dialogs():
        dialog_count += 1
    logger.info(f"Cached {dialog_count} dialogs.")

    # Explicitly resolve the two chats we care about, so we fail loudly at startup
    # (with a clear error) instead of silently at update-handling time.
    try:
        source_chat = await user.get_chat(config.SOURCE_CHANNEL_ID)
        logger.info(f"Resolved SOURCE_CHANNEL_ID -> {source_chat.title!r}")
    except Exception:
        logger.exception(
            f"Could not resolve SOURCE_CHANNEL_ID={config.SOURCE_CHANNEL_ID}. "
            f"Is the userbot account actually a member of this channel?"
        )

    try:
        proc_chat = await user.get_chat(config.PROCESSING_BOT_USERNAME)
        logger.info(f"Resolved PROCESSING_BOT_USERNAME -> {proc_chat.first_name!r}")
    except Exception:
        logger.exception(
            f"Could not resolve PROCESSING_BOT_USERNAME={config.PROCESSING_BOT_USERNAME}. "
            f"Has the userbot account sent /start to this bot at least once?"
        )

    logger.info("Bot and userbot clients started. Listening for videos...")

    try:
        await asyncio.Event().wait()  # run forever
    finally:
        await qm.stop()
        await bot.stop()
        await user.stop()
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
