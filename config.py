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

# SESSION_STRING: a real user account (logged in as a regular subscriber).
# Required because the source channel belongs to someone else who won't
# make our bot an admin there — Telegram only pushes channel updates to a
# BOT if it's an admin of that channel, but a normal user account can read
# a channel's posts just by being a member/subscriber. This same account
# also talks to the secondary link-converter bot, since Telegram does not
# allow bot-to-bot messaging.
#
# BOT_TOKEN: a dedicated bot account, admin in DESTINATION_CHANNEL_ID.
# Handles posting the final image+link there, and all admin commands
# (/setheader, /setfooter, /viewformat, /delheader, /delfooter).
SESSION_STRING = _get_env("SESSION_STRING")
BOT_TOKEN = _get_env("BOT_TOKEN")

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
