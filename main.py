"""
main.py
Entry point for bot 'V' (Telethon edition).

Runs TWO Telethon clients with distinct roles:

  userbot_client (SESSION_STRING) — a real user account:
      - listens to SOURCE_CHANNEL_ID for new videos (the channel belongs
        to someone else who won't make our bot an admin there, and a
        bot only gets channel updates if it IS an admin — a normal
        account just needs to be a subscriber)
      - talks to the secondary link-converter bot (Telegram does not
        allow bot-to-bot messaging, so this handshake must go through a
        real account)

  admin_bot_client (BOT_TOKEN) — a dedicated bot, admin in the
  destination channel:
      - posts the final image+caption to DESTINATION_CHANNEL_ID via the
        plain Telegram Bot HTTP API (NOT Telethon/MTProto) — bot accounts
        cannot call GetDialogsRequest over MTProto, so there is no way
        to warm an entity cache for them the way we do for the userbot;
        the plain HTTP API sidesteps that entirely since it only needs
        a bare chat_id.
      - handles all admin commands (/setheader, /setfooter, /viewformat,
        /delheader, /delfooter) via private chat

Initializes the Neon DB pool, registers handlers on the right client,
warms the peer cache for the source chat (userbot only — bots can't do
this), and starts the FIFO video-processing worker (which runs on the
userbot client but posts through the plain Bot API).
"""

import asyncio
import logging
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger("V.main")

import database
from config import (
    API_ID,
    API_HASH,
    BOT_TOKEN,
    SESSION_STRING,
    SOURCE_CHANNEL_ID,
)
import handlers
from video_processor import start_worker, stop_worker

APP_NAME = "V"


async def _warm_peer_cache(client: TelegramClient, chat_id: int, label: str) -> None:
    """
    Telethon needs to resolve a chat's entity at least once per session
    before it can reliably send to it by numeric ID — without this, the
    very first send to a "new" chat ID can fail with 'Cannot find any
    entity corresponding to ...' (ValueError), even if the account is a
    member/admin there. Calling get_entity() forces that resolution and
    caches it locally. (Only relevant for real user accounts — bot
    accounts can't call GetDialogsRequest to populate this cache at all.)
    """
    try:
        await client.get_entity(chat_id)
        logger.info(f"Resolved & cached {label} peer ({chat_id}).")
    except Exception:
        logger.exception(
            f"Could not resolve {label} ({chat_id}). "
            f"Make sure this account is a member of that chat."
        )


async def main() -> None:
    await database.init_db()

    # ---- Userbot client: source channel + secondary bot ----
    userbot_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await userbot_client.start()
    logger.info("Userbot client started (SESSION_STRING).")

    handlers.register_source(userbot_client)

    logger.info("Fetching userbot dialog list to populate entity cache...")
    await userbot_client.get_dialogs(limit=None)
    await _warm_peer_cache(userbot_client, SOURCE_CHANNEL_ID, "SOURCE_CHANNEL_ID")

    # ---- Admin bot client: admin commands only (destination posting
    # goes through the plain Bot HTTP API in video_processor.py) ----
    admin_bot_client = TelegramClient(f"{APP_NAME}_bot", API_ID, API_HASH)
    await admin_bot_client.start(bot_token=BOT_TOKEN)
    logger.info("Admin bot client started (BOT_TOKEN).")

    handlers.register_admin(admin_bot_client)

    start_worker(userbot_client)
    logger.info("Bot 'V' is up and running.")

    try:
        await asyncio.gather(
            userbot_client.run_until_disconnected(),
            admin_bot_client.run_until_disconnected(),
        )
    finally:
        stop_worker()
        await userbot_client.disconnect()
        await admin_bot_client.disconnect()
        await database.close_db()
        logger.info("Bot 'V' shut down cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Exiting.")
