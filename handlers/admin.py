"""
handlers/admin.py
Admin-only commands, restricted to ADMIN_IDS from the environment.

    /setheader <text>   -> overwrite header in Neon DB
    /setfooter <text>   -> overwrite footer in Neon DB
    /viewformat         -> preview current header + footer
    /delheader          -> clear header
    /delfooter          -> clear footer
"""

import logging

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode

import database
from config import ADMIN_IDS

logger = logging.getLogger("V.admin")

admin_filter = filters.user(ADMIN_IDS) & filters.private


def register(app: Client) -> None:

    @app.on_message(filters.command("start") & filters.private)
    async def start_cmd(client: Client, message: Message):
        if message.from_user and message.from_user.id in ADMIN_IDS:
            text = (
                "👋 *Bot 'V' is online.*\n\n"
                "Available commands:\n"
                "`/setheader <text>` — set caption header\n"
                "`/setfooter <text>` — set caption footer\n"
                "`/viewformat` — preview current header/footer\n"
                "`/delheader` — clear header\n"
                "`/delfooter` — clear footer"
            )
        else:
            text = "👋 Hello! This bot is private and only responds to its admins."
        await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    @app.on_message(filters.command("setheader") & admin_filter)
    async def set_header_cmd(client: Client, message: Message):
        text = _strip_command(message.text)
        if not text:
            await message.reply_text("Usage: `/setheader <text>`", parse_mode=ParseMode.MARKDOWN)
            return
        await database.set_header(text)
        await message.reply_text("✅ Header updated.")
        logger.info(f"Admin {message.from_user.id} updated header.")

    @app.on_message(filters.command("setfooter") & admin_filter)
    async def set_footer_cmd(client: Client, message: Message):
        text = _strip_command(message.text)
        if not text:
            await message.reply_text("Usage: `/setfooter <text>`", parse_mode=ParseMode.MARKDOWN)
            return
        await database.set_footer(text)
        await message.reply_text("✅ Footer updated.")
        logger.info(f"Admin {message.from_user.id} updated footer.")

    @app.on_message(filters.command("delheader") & admin_filter)
    async def del_header_cmd(client: Client, message: Message):
        await database.delete_header()
        await message.reply_text("🗑️ Header cleared.")
        logger.info(f"Admin {message.from_user.id} cleared header.")

    @app.on_message(filters.command("delfooter") & admin_filter)
    async def del_footer_cmd(client: Client, message: Message):
        await database.delete_footer()
        await message.reply_text("🗑️ Footer cleared.")
        logger.info(f"Admin {message.from_user.id} cleared footer.")

    @app.on_message(filters.command("viewformat") & admin_filter)
    async def view_format_cmd(client: Client, message: Message):
        header, footer = await database.get_format()
        preview = (
            "*Current format:*\n\n"
            f"{header if header else '_[no header set]_'}\n"
            "`[TENBOX_LINK]`\n"
            f"{footer if footer else '_[no footer set]_'}"
        )
        await message.reply_text(preview, parse_mode=ParseMode.MARKDOWN)

    # Silently ignore admin commands from non-admins in private chat, so
    # unauthorized users get no information leak about command existence.
    @app.on_message(
        filters.command(["setheader", "setfooter", "viewformat", "delheader", "delfooter"])
        & ~admin_filter
    )
    async def reject_non_admin(client: Client, message: Message):
        logger.info(f"Ignored command from unauthorized user {message.from_user.id if message.from_user else 'unknown'}.")
        # Intentionally no reply.


def _strip_command(text: str) -> str:
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""
