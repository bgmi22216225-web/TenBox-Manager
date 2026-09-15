"""
handlers/channel_listener.py
Two listeners:
  1. New videos posted in SOURCE_CHANNEL_ID -> enqueued into the pipeline.
  2. Any message from SECONDARY_BOT_USERNAME's chat -> handed to the
     pipeline in case it is the awaited reply for the current job.
"""

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from config import SOURCE_CHANNEL_ID, SECONDARY_BOT_USERNAME
from handlers.video_processor import enqueue_video, resolve_secondary_reply

logger = logging.getLogger("V.channel_listener")


def register(app: Client) -> None:

    @app.on_message(filters.chat(SOURCE_CHANNEL_ID) & filters.video)
    async def on_source_video(client: Client, message: Message):
        logger.info(f"New video detected in source channel (msg_id={message.id}).")
        enqueue_video(message)

    @app.on_message(filters.chat(SECONDARY_BOT_USERNAME) & filters.incoming)
    async def on_secondary_bot_reply(client: Client, message: Message):
        consumed = resolve_secondary_reply(message)
        if not consumed:
            logger.debug("Received message from secondary bot with no pending job — ignored.")

    @app.on_edited_message(filters.chat(SECONDARY_BOT_USERNAME) & filters.incoming)
    async def on_secondary_bot_edit(client: Client, message: Message):
        # Some bots edit their "Uploading..." status message into the
        # final result instead of sending a brand-new message.
        consumed = resolve_secondary_reply(message)
        if not consumed:
            logger.debug("Received edited message from secondary bot with no pending job — ignored.")
