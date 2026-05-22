"""Process-wide asyncpg pool + sync bridge helpers for Hermes's PG storage.

Owns the single `asyncpg.Pool` instance. All DB-accessing code in Hermes
goes through `connection()` or `transaction()`. Sync callers use
`run_sync(coro)` until they can be refactored to async.

Initialize once per process at entry-point startup (`init(dsn)`).
Close at shutdown (`close()`). Pool size is tunable via env vars:
    HERMES_PG_POOL_MIN (default 4)
    HERMES_PG_POOL_MAX (default 64)
"""

from __future__ import annotations

import asyncio
import os
import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Optional, TypeVar

import asyncpg

T = TypeVar("T")

_pool: Optional[asyncpg.Pool] = None
_pool_lock = threading.Lock()


async def init(
    dsn: str,
    *,
    min_size: Optional[int] = None,
    max_size: Optional[int] = None,
    command_timeout: int = 30,
) -> None:
    """Create the singleton pool. Idempotent: a second call is a no-op."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            return
        ms = min_size if min_size is not None else int(os.environ.get("HERMES_PG_POOL_MIN", "4"))
        Ms = max_size if max_size is not None else int(os.environ.get("HERMES_PG_POOL_MAX", "64"))
    pool = await asyncpg.create_pool(
        dsn,
        min_size=ms,
        max_size=Ms,
        command_timeout=command_timeout,
    )
    with _pool_lock:
        if _pool is None:
            _pool = pool
        else:
            await pool.close()  # lost the race; throw away the dup


async def close() -> None:
    global _pool
    with _pool_lock:
        p = _pool
        _pool = None
    if p is not None:
        await p.close()


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("hermes_db.init() not called")
    return _pool


@asynccontextmanager
async def connection() -> AsyncIterator[asyncpg.Connection]:
    async with pool().acquire() as conn:
        yield conn


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    async with pool().acquire() as conn:
        async with conn.transaction():
            yield conn


def run_sync(coro: Awaitable[T]) -> T:
    """Bridge a sync caller to an async DB call.

    Mirrors the proven pattern in `model_tools._run_async`. Must NOT be called
    from inside a running event loop — that indicates the caller is async and
    should `await` directly.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_running():
        raise RuntimeError(
            "hermes_db.run_sync called from inside running event loop; "
            "refactor caller to `await` the coroutine directly."
        )
    return loop.run_until_complete(coro)
