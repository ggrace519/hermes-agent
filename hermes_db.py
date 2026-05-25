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
import concurrent.futures
import json
import os
import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Optional, TypeVar

import asyncpg

T = TypeVar("T")

_pool: Optional[asyncpg.Pool] = None
_pool_lock = threading.Lock()

# Persistent module-level event loop for sync bridging. The asyncpg pool
# binds to whichever loop created it; reusing one loop across run_sync()
# calls keeps the pool valid across pytest tests (otherwise a per-call
# ``asyncio.new_event_loop`` leaves the pool bound to a closed loop and
# every subsequent call raises ``RuntimeError: Event loop is closed``).
# Mirrors the pattern in ``model_tools._get_tool_loop``.
_sync_loop: Optional[asyncio.AbstractEventLoop] = None
_sync_loop_lock = threading.Lock()

# Background single-thread executor used by ``run_sync`` when a different
# asyncio event loop is already running in the caller's thread (typical
# in pytest-asyncio tests that exercise sync wrappers around async DB
# calls). Python forbids ``loop.run_until_complete`` while ANY loop is
# running in the current thread, so we offload the call to this worker
# thread where no loop is running. The thread is created lazily on first
# need; ``max_workers=1`` keeps run_sync calls serialised on _sync_loop
# (which is the only loop we ever run inside the worker).
_sync_offload_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
_sync_offload_lock = threading.Lock()


def _get_sync_offload_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _sync_offload_executor
    with _sync_offload_lock:
        if _sync_offload_executor is None:
            _sync_offload_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="hermes-db-sync"
            )
        return _sync_offload_executor


def _get_sync_loop() -> asyncio.AbstractEventLoop:
    global _sync_loop
    with _sync_loop_lock:
        if _sync_loop is None or _sync_loop.is_closed():
            _sync_loop = asyncio.new_event_loop()
        return _sync_loop


async def _setup_jsonb_codec(conn):
    """Register JSONB codec so asyncpg returns Python objects for jsonb columns.

    Also registers a text-format codec for the pgvector ``vector`` type so
    asyncpg round-trips ``vector(N)`` columns as ``list[float]`` (Phase C —
    used by ``substrate_slices.embedding``). The vector extension may not
    be enabled yet on every database (pre-Phase-C deployments); we probe
    for it and skip registration silently if absent. This keeps the pool
    init path safe to run against older Alembic heads.
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    # Vector codec: optional. Skip when the extension isn't installed
    # (older deployments). Encoder formats as "[x,y,z,...]" — pgvector's
    # text input format. Decoder strips the brackets and splits on
    # commas. ~10 lines; no Python `pgvector` package required.
    try:
        await conn.set_type_codec(
            "vector",
            encoder=_encode_vector,
            decoder=_decode_vector,
            schema="public",
            format="text",
        )
    except (asyncpg.exceptions.UndefinedObjectError, ValueError):
        # Vector type isn't registered on this DB; skip silently. The
        # substrate recall pipeline will fail loudly when it tries to
        # write embeddings, which is the right place for the error.
        # asyncpg raises ValueError("unknown type: public.vector") when
        # introspection finds no matching entry in pg_type — older
        # deployments that haven't run the Phase C migration yet.
        pass


def _encode_vector(vec) -> str:
    """Encode a Python sequence of floats as pgvector's text input
    format, e.g. ``[0.1,0.2,0.3]``. Uses ``repr(float(x))`` so floats
    round-trip without rounding (Python's repr is exact for floats)."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _decode_vector(s: str) -> list:
    """Decode pgvector's text output ``[x,y,z,...]`` into a list of
    Python floats. asyncpg passes the raw string; we strip brackets and
    split."""
    inner = s.strip().strip("[]")
    if not inner:
        return []
    return [float(x) for x in inner.split(",")]


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
        init=_setup_jsonb_codec,
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
        # Lazy bootstrap: if a DSN is in the environment but init() was never
        # called, initialise the pool on first use. This lets CLI subcommands
        # that don't touch the DB (e.g. ``hermes --help``, ``hermes version``)
        # run in environments without a live PG instance, while DB-touching
        # subcommands still get a working pool without an explicit init step
        # at the entry point.
        dsn = os.environ.get("HERMES_PG_DSN")
        if dsn:
            loop = _get_sync_loop()
            if not loop.is_running():
                loop.run_until_complete(init(dsn))
                if _pool is not None:
                    return _pool
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

    Mirrors the proven pattern in `model_tools._run_async`. Uses a
    persistent module-level event loop (``_get_sync_loop``) so the
    asyncpg pool stays bound to a live loop across calls. Per-call
    ``asyncio.new_event_loop()`` would orphan the pool against a closed
    loop after the first call and surface as ``RuntimeError: Event loop
    is closed`` on the next acquire.

    Three calling contexts:

    1. **Pure sync caller** (CLI subcommand, gateway-runner sync entry,
       smoke test): no asyncio loop is running in the current thread.
       We drive ``_sync_loop.run_until_complete(coro)`` directly.
    2. **Inside _sync_loop itself** (re-entrant call): ``_sync_loop`` is
       already running. This is a true bug — the caller should ``await``
       the coroutine — so we raise.
    3. **Inside a *different* loop** (pytest-asyncio test body that calls
       sync ``kb.*`` helpers, ACP server scheduled callbacks): another
       loop is running in this thread but not ``_sync_loop``. Python's
       asyncio still forbids running a second loop in the same thread,
       so we offload the ``run_until_complete`` call to a dedicated
       background thread where no loop is running. The pool stays on
       ``_sync_loop`` (asyncpg cares about loop, not thread) so this is
       safe.

    NOTE: this function does NOT lazy-bootstrap the pool — auto-init
    lives in ``pool()`` for sync code paths that touch the DB outside
    an event loop, and ``ensure_pool_sync()`` for code that knows it's
    about to acquire connections from inside ``run_sync``.
    """
    loop = _get_sync_loop()
    if loop.is_running():
        # Case 2: re-entrant into our own sync loop.
        raise RuntimeError(
            "hermes_db.run_sync called from inside its own sync loop; "
            "refactor caller to `await` the coroutine directly."
        )

    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None

    if current is None:
        # Case 1: pure sync context, run on this thread.
        return loop.run_until_complete(coro)

    # Case 3: a different loop is running in this thread (pytest-asyncio,
    # ACP server callback, etc.). Offload to a worker thread so Python's
    # one-loop-per-thread check doesn't reject us.
    executor = _get_sync_offload_executor()
    future = executor.submit(loop.run_until_complete, coro)
    return future.result()


def ensure_pool_sync() -> bool:
    """Initialise the pool from ``HERMES_PG_DSN`` if it hasn't been already.

    Returns True if a pool is available after the call, False if no DSN is
    configured and no pool exists. Safe to call from any sync context;
    must NOT be called from inside a running event loop.

    Intended for sync entry points that are about to call ``run_sync`` on
    a DB-touching coroutine. Calling this once at the top of e.g. an
    ACP ``_get_db`` ensures the inner ``async with connection()`` doesn't
    hit the "init not called" error.
    """
    if _pool is not None:
        return True
    dsn = os.environ.get("HERMES_PG_DSN")
    if not dsn:
        return False
    # Reject calls from inside ANY running loop on this thread (not just
    # our own persistent ``_sync_loop``). pytest-asyncio creates a
    # per-test loop that's distinct from our persistent one but still
    # owns the thread; ``run_until_complete`` would raise "Cannot run
    # the event loop while another loop is running" if we tried to
    # bootstrap synchronously from inside such a test.
    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False
    if running:
        raise RuntimeError(
            "hermes_db.ensure_pool_sync called from inside a running event "
            "loop; await hermes_db.init(dsn) directly instead."
        )
    loop = _get_sync_loop()
    coro = init(dsn)
    try:
        loop.run_until_complete(coro)
    except BaseException:
        # ``init`` may have set up partial state; close the coroutine
        # explicitly so the leak warning doesn't fire.
        coro.close()
        raise
    return _pool is not None
