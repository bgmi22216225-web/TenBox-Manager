"""
config.py
Loads and validates all environment variables required by bot 'V'.
Fails fast with a clear error if a required variable is missing/invalid.
"""

import os
import sys
import logging

logger = logging.getLogger("V.config")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv is optional in production (Railway injects env vars directly)
    pass


def _get_env(name: str, required: bool = True, default=None):
    value = os.environ.get(name, default)
    if required and (value is None or str(value).strip() == ""):
        logger.critical(f"Missing required environment variable: {name}")
        sys.exit(1)
    return value


def _parse_int_list(raw: str) -> list[int]:
    if not raw:
        return []
    result = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            result.append(int(chunk))
        except ValueError:
            logger.critical(f"Invalid integer in ADMIN_IDS: '{chunk}'")
            sys.exit(1)
    return result


# ---- Telegram credentials ----
API_ID = int(_get_env("API_ID"))
API_HASH = _get_env("API_HASH")

# Either BOT_TOKEN (bot account) or SESSION_STRING (userbot account) must be set.
BOT_TOKEN = _get_env("BOT_TOKEN", required=False)
SESSION_STRING = _get_env("SESSION_STRING", required=False)

if not BOT_TOKEN and not SESSION_STRING:
    logger.critical("Either BOT_TOKEN or SESSION_STRING must be set.")
    sys.exit(1)

# ---- Database ----
DATABASE_URL = _get_env("DATABASE_URL")

# ---- Channels / bot ----
SOURCE_CHANNEL_ID = int(_get_env("SOURCE_CHANNEL_ID"))
DESTINATION_CHANNEL_ID = int(_get_env("DESTINATION_CHANNEL_ID"))
SECONDARY_BOT_USERNAME = _get_env("SECONDARY_BOT_USERNAME").lstrip("@")

# ---- Admins ----
ADMIN_IDS = _parse_int_list(_get_env("ADMIN_IDS"))
if not ADMIN_IDS:
    logger.critical("ADMIN_IDS must contain at least one valid Telegram user ID.")
    sys.exit(1)

# ---- Tunables (optional, sensible defaults) ----
SECONDARY_BOT_TIMEOUT = int(_get_env("SECONDARY_BOT_TIMEOUT", required=False, default=90))
LINK_FILTER_KEYWORD = _get_env("LINK_FILTER_KEYWORD", required=False, default="tenbox").lower()
TEMP_DIR = _get_env("TEMP_DIR", required=False, default="/tmp/v_bot")

os.makedirs(TEMP_DIR, exist_ok=True)
