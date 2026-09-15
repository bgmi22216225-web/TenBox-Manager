"""
main.py
Entry point for bot 'V' (Telethon edition).

Starts the Telethon client (bot token OR userbot session string),
initializes the Neon DB pool, registers all handlers, warms the peer
cache for the source + destination chats (this is what fixes the
KeyError/ValueError when sending to a chat the session hasn't resolved
yet), and starts the FIFO video-processing worker.
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
    DESTINATION_BOT_TOKEN,
    SOURCE_CHANNEL_ID,
    DESTINATION_CHANNEL_ID,
)
import handlers
from video_processor import start_worker, stop_worker, set_destination_client

APP_NAME = "V"


def build_client() -> TelegramClient:
    if SESSION_STRING:
        logger.info("Starting as a USERBOT (SESSION_STRING detected).")
        return TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    logger.info("Starting as a BOT (BOT_TOKEN detected).")
    return TelegramClient(APP_NAME, API_ID, API_HASH)


async def _warm_peer_cache(client: TelegramClient, chat_id: int, label: str) -> None:
    """
    Telethon needs to resolve a chat's entity at least once per session
    before it can reliably send to it by numeric ID — without this, the
    very first send to a "new" chat ID can fail with 'Cannot find any
    entity corresponding to ...' (ValueError) or a KeyError deep inside
    Telethon's entity cache, even if the account is a member/admin there.
    Calling get_entity() forces that resolution and caches it locally.
    """
    try:
        await client.get_entity(chat_id)
        logger.info(f"Resolved & cached {label} peer ({chat_id}).")
    except Exception:
        logger.exception(
            f"Could not resolve {label} ({chat_id}). "
            f"Make sure this account is a member (and, for the destination, an admin) of that chat."
        )


async def main() -> None:
    await database.init_db()

    client = build_client()

    dest_client = None
    if DESTINATION_BOT_TOKEN:
        logger.info("DESTINATION_BOT_TOKEN detected — using a dedicated bot to post to the destination channel.")
        dest_client = TelegramClient(f"{APP_NAME}_dest", API_ID, API_HASH)
        await dest_client.start(bot_token=DESTINATION_BOT_TOKEN)
        set_destination_client(dest_client)

    if SESSION_STRING:
        await client.start()
    else:
        await client.start(bot_token=BOT_TOKEN)

    handlers.register(client)

    # Warm the peer cache for every chat we'll need to send/read from.
    await _warm_peer_cache(client, SOURCE_CHANNEL_ID, "SOURCE_CHANNEL_ID")
    if dest_client is not None:
        await _warm_peer_cache(dest_client, DESTINATION_CHANNEL_ID, "DESTINATION_CHANNEL_ID")
    else:
        await _warm_peer_cache(client, DESTINATION_CHANNEL_ID, "DESTINATION_CHANNEL_ID")

    start_worker(client)
    logger.info("Bot 'V' is up and running.")

    try:
        await client.run_until_disconnected()
    finally:
        stop_worker()
        await client.disconnect()
        if dest_client is not None:
            await dest_client.disconnect()
        await database.close_db()
        logger.info("Bot 'V' shut down cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Exiting.")
