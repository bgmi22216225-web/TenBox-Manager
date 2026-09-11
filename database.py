"""
database.py

Async PostgreSQL (Neon) data layer for the bot.
Handles connection pooling, schema bootstrap, admin management, and
header/footer template storage.
"""

import logging
from typing import Optional, List

import asyncpg

logger = logging.getLogger("database")

_pool: Optional[asyncpg.Pool] = None

TEMPLATE_HEADER_KEY = "header"
TEMPLATE_FOOTER_KEY = "footer"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS admins (
    user_id     BIGINT PRIMARY KEY,
    added_by    BIGINT,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS templates (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


async def init_db(database_url: str, super_admin_id: int) -> None:
    """Create the connection pool, bootstrap schema, and ensure the
    super admin is present in the admins table."""
    global _pool
    logger.info("Connecting to Neon PostgreSQL...")
    _pool = await asyncpg.create_pool(
        dsn=database_url,
        min_size=1,
        max_size=5,
        command_timeout=30,
    )
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
        await conn.execute(
            """
            INSERT INTO admins (user_id, added_by)
            VALUES ($1, $1)
            ON CONFLICT (user_id) DO NOTHING
            """,
            super_admin_id,
        )
    logger.info("Database ready.")


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed.")


def _get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_db() first.")
    return _pool


# ---------------------------------------------------------------------------
# Admin management
# ---------------------------------------------------------------------------

async def is_admin(user_id: int) -> bool:
    pool = _get_pool()
    row = await pool.fetchrow("SELECT 1 FROM admins WHERE user_id = $1", user_id)
    return row is not None


async def add_admin(user_id: int, added_by: int) -> bool:
    """Returns True if a new row was inserted, False if already an admin."""
    pool = _get_pool()
    result = await pool.execute(
        """
        INSERT INTO admins (user_id, added_by)
        VALUES ($1, $2)
        ON CONFLICT (user_id) DO NOTHING
        """,
        user_id,
        added_by,
    )
    # asyncpg execute() returns a tag like "INSERT 0 1" or "INSERT 0 0"
    return result.endswith(" 1")


async def remove_admin(user_id: int) -> bool:
    """Returns True if a row was deleted."""
    pool = _get_pool()
    result = await pool.execute("DELETE FROM admins WHERE user_id = $1", user_id)
    return result.endswith(" 1")


async def list_admins() -> List[int]:
    pool = _get_pool()
    rows = await pool.fetch("SELECT user_id FROM admins ORDER BY added_at ASC")
    return [r["user_id"] for r in rows]


# ---------------------------------------------------------------------------
# Header / footer templates
# ---------------------------------------------------------------------------

async def get_template(key: str) -> Optional[str]:
    pool = _get_pool()
    row = await pool.fetchrow("SELECT value FROM templates WHERE key = $1", key)
    return row["value"] if row else None


async def set_template(key: str, value: str) -> None:
    pool = _get_pool()
    await pool.execute(
        """
        INSERT INTO templates (key, value, updated_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """,
        key,
        value,
    )


async def delete_template(key: str) -> bool:
    pool = _get_pool()
    result = await pool.execute("DELETE FROM templates WHERE key = $1", key)
    return result.endswith(" 1")


async def get_header() -> Optional[str]:
    return await get_template(TEMPLATE_HEADER_KEY)


async def get_footer() -> Optional[str]:
    return await get_template(TEMPLATE_FOOTER_KEY)
