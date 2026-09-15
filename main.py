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
from config import API_ID, API_HASH, BOT_TOKEN, SESSION_STRING
from handlers import admin, channel_listener
from handlers.video_processor import start_worker, stop_worker

APP_NAME = "V"


def build_client() -> Client:
    if SESSION_STRING:
        logger.info("Starting as a USERBOT (SESSION_STRING detected).")
        return Client(APP_NAME, api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)
    logger.info("Starting as a BOT (BOT_TOKEN detected).")
    return Client(APP_NAME, api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


async def main() -> None:
    await database.init_db()

    app = build_client()
    admin.register(app)
    channel_listener.register(app)

    await app.start()
    start_worker(app)
    logger.info("Bot 'V' is up and running.")

    try:
        await asyncio.Event().wait()  # run forever
    finally:
        stop_worker()
        await app.stop()
        await database.close_db()
        logger.info("Bot 'V' shut down cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Exiting.")
