"""
database.py
Neon PostgreSQL access layer using asyncpg.

Schema (single table, key-value style — only two keys are ever used:
'header' and 'footer'):

    CREATE TABLE IF NOT EXISTS bot_config (
        key   TEXT PRIMARY KEY,
        value TEXT
    );

set_header()/set_footer() overwrite the existing row completely (UPSERT).
"""

import logging
import asyncpg

from config import DATABASE_URL

logger = logging.getLogger("V.database")

_pool: asyncpg.Pool | None = None

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS bot_config (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_UPSERT_SQL = """
INSERT INTO bot_config (key, value)
VALUES ($1, $2)
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
"""

_SELECT_SQL = "SELECT value FROM bot_config WHERE key = $1;"
_DELETE_SQL = "DELETE FROM bot_config WHERE key = $1;"


async def init_db() -> None:
    """Create the connection pool and ensure the schema exists. Call once at startup."""
    global _pool
    _pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=5)
    async with _pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE_SQL)
    logger.info("Database pool initialized and schema verified.")


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        logger.info("Database pool closed.")


async def _get(key: str) -> str | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(_SELECT_SQL, key)
        return row["value"] if row else None


async def _set(key: str, value: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(_UPSERT_SQL, key, value)


async def _delete(key: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(_DELETE_SQL, key)


# ---- Public API ----

async def get_header() -> str:
    return await _get("header") or ""


async def get_footer() -> str:
    return await _get("footer") or ""


async def set_header(text: str) -> None:
    await _set("header", text)
    logger.info("Header updated.")


async def set_footer(text: str) -> None:
    await _set("footer", text)
    logger.info("Footer updated.")


async def delete_header() -> None:
    await _delete("header")
    logger.info("Header cleared.")


async def delete_footer() -> None:
    await _delete("footer")
    logger.info("Footer cleared.")


async def get_format() -> tuple[str, str]:
    """Returns (header, footer) in one call."""
    header = await get_header()
    footer = await get_footer()
    return header, footer
