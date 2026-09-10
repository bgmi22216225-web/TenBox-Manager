"""
config.py
Centralized environment variable loading + validation.
All modules import settings from here instead of reading os.environ directly.
"""

import os
import sys
import logging

logger = logging.getLogger("config")


def _get_env(name: str, required: bool = True, default=None):
    value = os.environ.get(name, default)
    if required and (value is None or value == ""):
        logger.critical(f"Missing required environment variable: {name}")
        sys.exit(1)
    return value


def _get_int_env(name: str, required: bool = True, default=None):
    value = _get_env(name, required=required, default=default)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.critical(f"Environment variable {name} must be an integer, got: {value!r}")
        sys.exit(1)


# --- Telegram API credentials ---
API_ID = _get_int_env("API_ID")
API_HASH = _get_env("API_HASH")
BOT_TOKEN = _get_env("BOT_TOKEN")
SESSION_STRING = _get_env("SESSION_STRING")  # userbot session, required for forwarding/listening

# --- Admin / channel config ---
MAIN_ADMIN_ID = _get_int_env("MAIN_ADMIN_ID")
SOURCE_CHANNEL_ID = _get_int_env("SOURCE_CHANNEL_ID")
DESTINATION_CHANNEL_ID = _get_int_env("DESTINATION_CHANNEL_ID")

# PROCESSING_BOT_USERNAME can be with or without leading @
PROCESSING_BOT_USERNAME = _get_env("PROCESSING_BOT_USERNAME").lstrip("@")

# --- Database ---
DATABASE_URL = _get_env("DATABASE_URL")  # Neon PostgreSQL connection URI

# --- Tunable behavior (all optional, sane defaults) ---
# How long (seconds) to wait for the processing bot's reply before giving up on a video.
RESPONSE_TIMEOUT = int(os.environ.get("RESPONSE_TIMEOUT", "120"))

# How many videos can be *downloading/processing* concurrently.
# Kept at 1 by default to guarantee strict FIFO ordering end-to-end; the design
# doc explicitly calls for zero mismatch, so raising this needs the reply-matching
# logic in queue_manager.py to be trusted fully — see comments there.
QUEUE_WORKER_CONCURRENCY = int(os.environ.get("QUEUE_WORKER_CONCURRENCY", "1"))

# Local scratch directory for downloaded videos / extracted snapshots (cleaned up per-item).
WORK_DIR = os.environ.get("WORK_DIR", "/tmp/bot_workdir")

os.makedirs(WORK_DIR, exist_ok=True)
