"""
database.py
Neon PostgreSQL access layer using asyncpg.
Owns: connection pool lifecycle, schema creation, and all read/write
helpers for bot_settings and admin_users.
"""

import logging
import asyncpg

from config import DATABASE_URL, MAIN_ADMIN_ID

logger = logging.getLogger("database")

_pool: asyncpg.Pool | None = None

CREATE_BOT_SETTINGS_SQL = """
CREATE TABLE IF NOT EXISTS bot_settings (
    id INTEGER PRIMARY KEY DEFAULT 1,
    header_text TEXT NOT NULL DEFAULT '',
    footer_text TEXT NOT NULL DEFAULT ''
);
"""

CREATE_ADMIN_USERS_SQL = """
CREATE TABLE IF NOT EXISTS admin_users (
    user_id BIGINT PRIMARY KEY,
    added_by BIGINT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""

ENSURE_SETTINGS_ROW_SQL = """
INSERT INTO bot_settings (id, header_text, footer_text)
VALUES (1, '', '')
ON CONFLICT (id) DO NOTHING;
"""


async def init_pool():
    """Create the asyncpg pool and make sure the schema exists. Call once at startup."""
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=DATABASE_URL,
        min_size=1,
        max_size=5,
        command_timeout=30,
    )
    async with _pool.acquire() as conn:
        await conn.execute(CREATE_BOT_SETTINGS_SQL)
        await conn.execute(CREATE_ADMIN_USERS_SQL)
        await conn.execute(ENSURE_SETTINGS_ROW_SQL)
    logger.info("Database pool initialized and schema verified.")


async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        logger.info("Database pool closed.")


def _require_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool() first.")
    return _pool


# ---------- bot_settings (header / footer) ----------

async def get_header() -> str:
    pool = _require_pool()
    row = await pool.fetchrow("SELECT header_text FROM bot_settings WHERE id = 1;")
    return row["header_text"] if row else ""


async def get_footer() -> str:
    pool = _require_pool()
    row = await pool.fetchrow("SELECT footer_text FROM bot_settings WHERE id = 1;")
    return row["footer_text"] if row else ""


async def set_header(text: str) -> None:
    """Overwrite header_text exactly as given (preserves emojis, line breaks, links)."""
    pool = _require_pool()
    await pool.execute(
        """
        INSERT INTO bot_settings (id, header_text, footer_text)
        VALUES (1, $1, '')
        ON CONFLICT (id) DO UPDATE SET header_text = EXCLUDED.header_text;
        """,
        text,
    )


async def set_footer(text: str) -> None:
    """Overwrite footer_text exactly as given (preserves emojis, line breaks, links)."""
    pool = _require_pool()
    await pool.execute(
        """
        INSERT INTO bot_settings (id, header_text, footer_text)
        VALUES (1, '', $1)
        ON CONFLICT (id) DO UPDATE SET footer_text = EXCLUDED.footer_text;
        """,
        text,
    )


# ---------- admin_users ----------

async def add_admin(user_id: int, added_by: int) -> bool:
    """Returns True if inserted, False if the user was already an admin."""
    pool = _require_pool()
    result = await pool.execute(
        """
        INSERT INTO admin_users (user_id, added_by)
        VALUES ($1, $2)
        ON CONFLICT (user_id) DO NOTHING;
        """,
        user_id,
        added_by,
    )
    # asyncpg execute() returns a status string like "INSERT 0 1" (1 row) or "INSERT 0 0" (conflict, no-op)
    return result.endswith(" 1")


async def remove_admin(user_id: int) -> bool:
    """Returns True if a row was deleted, False if user wasn't an admin."""
    pool = _require_pool()
    result = await pool.execute("DELETE FROM admin_users WHERE user_id = $1;", user_id)
    return result.endswith(" 1")


async def list_admins() -> list[dict]:
    pool = _require_pool()
    rows = await pool.fetch("SELECT user_id, added_by, created_at FROM admin_users ORDER BY created_at;")
    return [dict(r) for r in rows]


async def is_admin(user_id: int) -> bool:
    """True for the main admin OR any admin stored in admin_users."""
    if user_id == MAIN_ADMIN_ID:
        return True
    pool = _require_pool()
    row = await pool.fetchrow("SELECT 1 FROM admin_users WHERE user_id = $1;", user_id)
    return row is not None


def is_main_admin(user_id: int) -> bool:
    return user_id == MAIN_ADMIN_ID
