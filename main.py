"""
main.py
Entry point for bot 'V'.

Starts the Pyrogram client (bot token OR userbot session string),
initializes the Neon DB pool, registers all handlers, and starts the
FIFO video-processing worker.
"""

import asyncio
import logging

from pyrogram import Client

from utils.logger import setup_logging
setup_logging()
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
from handlers import admin, channel_listener
from handlers.video_processor import start_worker, stop_worker, set_destination_client

APP_NAME = "V"


def build_client() -> Client:
    if SESSION_STRING:
        logger.info("Starting as a USERBOT (SESSION_STRING detected).")
        return Client(APP_NAME, api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)
    logger.info("Starting as a BOT (BOT_TOKEN detected).")
    return Client(APP_NAME, api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


async def _warm_peer_cache(client: Client, chat_id: int, label: str) -> None:
    """
    Pyrogram can't send to a chat by numeric ID until it has resolved
    that chat's peer at least once in this session. Calling get_chat()
    forces that resolution and caches it locally — without this, the
    very first send to a "new" chat ID fails with
    'Peer id invalid' / 'ID not found', even if the account is a
    member/admin there.
    """
    try:
        await client.get_chat(chat_id)
        logger.info(f"Resolved & cached {label} peer ({chat_id}).")
    except Exception:
        logger.exception(
            f"Could not resolve {label} ({chat_id}). "
            f"Make sure this account is a member (and, for the destination, an admin) of that chat."
        )


async def main() -> None:
    await database.init_db()

    app = build_client()
    admin.register(app)
    channel_listener.register(app)

    dest_app = None
    if DESTINATION_BOT_TOKEN:
        logger.info("DESTINATION_BOT_TOKEN detected — using a dedicated bot to post to the destination channel.")
        dest_app = Client(f"{APP_NAME}_dest", api_id=API_ID, api_hash=API_HASH, bot_token=DESTINATION_BOT_TOKEN)
        await dest_app.start()
        set_destination_client(dest_app)

    await app.start()

    # Warm the peer cache for every chat we'll need to send/read from.
    await _warm_peer_cache(app, SOURCE_CHANNEL_ID, "SOURCE_CHANNEL_ID")
    if dest_app is not None:
        await _warm_peer_cache(dest_app, DESTINATION_CHANNEL_ID, "DESTINATION_CHANNEL_ID")
    else:
        await _warm_peer_cache(app, DESTINATION_CHANNEL_ID, "DESTINATION_CHANNEL_ID")

    start_worker(app)
    logger.info("Bot 'V' is up and running.")

    try:
        await asyncio.Event().wait()  # run forever
    finally:
        stop_worker()
        await app.stop()
        if dest_app is not None:
            await dest_app.stop()
        await database.close_db()
        logger.info("Bot 'V' shut down cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Exiting.")
