"""
handlers.py
All Telethon event handlers for bot 'V':
  - Admin-only commands (/start, /setheader, /setfooter, /viewformat, /delheader, /delfooter)
  - Source channel video listener
  - Secondary bot reply listener (new AND edited messages)
"""

import logging

from telethon import TelegramClient, events

import database
from config import ADMIN_IDS, SOURCE_CHANNEL_ID, SECONDARY_BOT_USERNAME
from video_processor import enqueue_video, resolve_secondary_reply

logger = logging.getLogger("V.handlers")


def _is_admin(event) -> bool:
    return event.sender_id in ADMIN_IDS


def _strip_command(text: str) -> str:
    parts = (text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def register(client: TelegramClient) -> None:

    # ---- Admin commands (private chat only) ----

    @client.on(events.NewMessage(pattern=r"^/start", func=lambda e: e.is_private))
    async def start_cmd(event):
        if _is_admin(event):
            text = (
                "👋 **Bot 'V' is online.**\n\n"
                "Available commands:\n"
                "`/setheader <text>` — set caption header\n"
                "`/setfooter <text>` — set caption footer\n"
                "`/viewformat` — preview current header/footer\n"
                "`/delheader` — clear header\n"
                "`/delfooter` — clear footer"
            )
        else:
            text = "👋 Hello! This bot is private and only responds to its admins."
        await event.reply(text, parse_mode="md")

    @client.on(events.NewMessage(pattern=r"^/setheader", func=lambda e: e.is_private))
    async def set_header_cmd(event):
        if not _is_admin(event):
            logger.info(f"Ignored /setheader from unauthorized user {event.sender_id}.")
            return
        text = _strip_command(event.raw_text)
        if not text:
            await event.reply("Usage: `/setheader <text>`", parse_mode="md")
            return
        await database.set_header(text)
        await event.reply("✅ Header updated.")
        logger.info(f"Admin {event.sender_id} updated header.")

    @client.on(events.NewMessage(pattern=r"^/setfooter", func=lambda e: e.is_private))
    async def set_footer_cmd(event):
        if not _is_admin(event):
            logger.info(f"Ignored /setfooter from unauthorized user {event.sender_id}.")
            return
        text = _strip_command(event.raw_text)
        if not text:
            await event.reply("Usage: `/setfooter <text>`", parse_mode="md")
            return
        await database.set_footer(text)
        await event.reply("✅ Footer updated.")
        logger.info(f"Admin {event.sender_id} updated footer.")

    @client.on(events.NewMessage(pattern=r"^/delheader", func=lambda e: e.is_private))
    async def del_header_cmd(event):
        if not _is_admin(event):
            logger.info(f"Ignored /delheader from unauthorized user {event.sender_id}.")
            return
        await database.delete_header()
        await event.reply("🗑️ Header cleared.")
        logger.info(f"Admin {event.sender_id} cleared header.")

    @client.on(events.NewMessage(pattern=r"^/delfooter", func=lambda e: e.is_private))
    async def del_footer_cmd(event):
        if not _is_admin(event):
            logger.info(f"Ignored /delfooter from unauthorized user {event.sender_id}.")
            return
        await database.delete_footer()
        await event.reply("🗑️ Footer cleared.")
        logger.info(f"Admin {event.sender_id} cleared footer.")

    @client.on(events.NewMessage(pattern=r"^/viewformat", func=lambda e: e.is_private))
    async def view_format_cmd(event):
        if not _is_admin(event):
            logger.info(f"Ignored /viewformat from unauthorized user {event.sender_id}.")
            return
        header, footer = await database.get_format()
        preview = (
            "**Current format:**\n\n"
            f"{header if header else '_[no header set]_'}\n"
            "`[TENBOX_LINK]`\n"
            f"{footer if footer else '_[no footer set]_'}"
        )
        await event.reply(preview, parse_mode="md")

    # ---- Source channel: new videos ----

    @client.on(events.NewMessage(chats=SOURCE_CHANNEL_ID))
    async def on_source_video(event):
        if not event.video:
            return
        logger.info(f"New video detected in source channel (msg_id={event.id}).")
        enqueue_video(event.message)

    # ---- Secondary bot: replies (new + edited) ----

    @client.on(events.NewMessage(chats=SECONDARY_BOT_USERNAME, incoming=True))
    async def on_secondary_bot_reply(event):
        if not resolve_secondary_reply(event.message):
            logger.debug("Received message from secondary bot with no pending job — ignored.")

    @client.on(events.MessageEdited(chats=SECONDARY_BOT_USERNAME, incoming=True))
    async def on_secondary_bot_edit(event):
        # Some bots edit their "Uploading..." status message into the
        # final result instead of sending a brand-new message.
        if not resolve_secondary_reply(event.message):
            logger.debug("Received edited message from secondary bot with no pending job — ignored.")
