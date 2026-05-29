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


def _apply_tcp_keepalive(conn) -> None:
    """Enable TCP keepalives on a freshly-created asyncpg pool connection.

    asyncpg disables SO_KEEPALIVE by default.  Without keepalives, when the
    remote end (postgres, docker bridge, NAT) closes a connection while it is
    idle in the pool, the OS never sends a RST to the client — the socket
    stays in CLOSE_WAIT with the FIN buffered but unread.  asyncpg's
    ``is_closed()`` only checks its internal protocol state (not the OS
    socket), so it returns False and the pool hands the dead connection out.
    The next caller gets ``ConnectionDoesNotExistError: connection was closed
    in the middle of operation`` and an orphaned "Future exception was never
    retrieved" log line.

    With keepalives enabled, the OS probes the peer after
    ``TCP_KEEPIDLE`` seconds of silence.  If ``TCP_KEEPCNT`` consecutive
    probes (``TCP_KEEPINTVL`` seconds apart) go unanswered, the OS closes the
    socket, asyncpg's transport fires ``connection_lost``, and the pool evicts
    the connection — all before any caller ever acquires it.

    Called from the pool's ``init`` hook so every connection (including
    replacements after recycling) gets keepalives.  Values are intentionally
    aggressive for a local/LAN postgres: 10s idle, 5s interval, 3 probes =
    dead connection detected in ≤25 seconds.  All tunable via env vars.
    """
    import socket as _socket

    try:
        raw = conn._transport.get_extra_info("socket")
        if raw is None:
            return
        raw.setsockopt(_socket.SOL_SOCKET, _socket.SO_KEEPALIVE, 1)
        keepidle = int(os.environ.get("HERMES_PG_KEEPIDLE_S", "10"))
        keepintvl = int(os.environ.get("HERMES_PG_KEEPINTVL_S", "5"))
        keepcnt = int(os.environ.get("HERMES_PG_KEEPCNT", "3"))
        # TCP_KEEPIDLE / TCP_KEEPINTVL / TCP_KEEPCNT are Linux-specific;
        # skip gracefully on macOS/Windows where they may not exist.
        if hasattr(_socket, "TCP_KEEPIDLE"):
            raw.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_KEEPIDLE, keepidle)
        if hasattr(_socket, "TCP_KEEPINTVL"):
            raw.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_KEEPINTVL, keepintvl)
        if hasattr(_socket, "TCP_KEEPCNT"):
            raw.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_KEEPCNT, keepcnt)
    except Exception:
        # Never crash pool creation over a keepalive failure.
        pass


async def _setup_jsonb_codec(conn):
    """Register JSONB codec so asyncpg returns Python objects for jsonb columns.

    Also registers a text-format codec for the pgvector ``vector`` type so
    asyncpg round-trips ``vector(N)`` columns as ``list[float]`` (Phase C —
    used by ``substrate_slices.embedding``). The vector extension may not
    be enabled yet on every database (pre-Phase-C deployments); we probe
    for it and skip registration silently if absent. This keeps the pool
    init path safe to run against older Alembic heads.

    Also enables TCP keepalives on the connection's socket so dead connections
    are detected by the OS and evicted from the pool before any caller
    acquires them (see ``_apply_tcp_keepalive`` for rationale).
    """
    _apply_tcp_keepalive(conn)
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


def _ssl_kwarg_for_dsn(dsn: str) -> dict:
    """Decide the ``ssl=`` kwarg for ``asyncpg.create_pool(...)``.

    Two distinct goals:

    1. **Production correctness.** Remote Postgres (Neon, Supabase, RDS,
       any DSN that doesn't resolve to a loopback / docker-compose host)
       should use asyncpg's default SSL negotiation. Operators who set
       ``sslmode=`` explicitly in the DSN have their preference honoured
       by asyncpg's own parsing — we don't override.

    2. **CI / local test stability.** When the DSN points at a known
       local plain-text Postgres (the docker-compose ``postgres`` service
       at ``localhost`` / ``127.0.0.1`` / ``postgres``) and the user did
       NOT specify ``sslmode=`` themselves, force ``ssl=False`` to skip
       asyncpg's SSL upgrade negotiation. That negotiation runs
       ``_create_ssl_connection`` even for ``sslmode=prefer``-then-
       downgrade, and the SSL context setup intermittently segfaults
       inside CPython's ssl module on GitHub Actions runners — taking
       the whole pytest worker down with no Python traceback. The local
       compose PG has no SSL anyway; this is the negotiation we want to
       skip.

    Returns ``{"ssl": False}`` when we want the skip, ``{}`` otherwise
    so we don't override asyncpg's default-or-DSN-derived behaviour.
    """
    dsn_lower = dsn.lower()
    # User-pinned sslmode in the DSN → respect it, no override.
    if "sslmode=" in dsn_lower:
        return {}
    # Extract the host segment between "@" and the trailing ":port/db".
    # urllib.parse handles all the edge cases (auth-with-@, ipv6, empty
    # password, etc.) so we don't have to reinvent them.
    try:
        from urllib.parse import urlparse
        host = (urlparse(dsn_lower).hostname or "").strip()
    except Exception:
        return {}
    if not host:
        return {}
    # Loopback aliases — always local.
    if host in ("localhost", "127.0.0.1", "::1"):
        return {"ssl": False}
    # Docker-compose service hostnames are single-label (no dots):
    # ``postgres``, ``postgres-test``, ``db``, etc. Public DNS names
    # always have at least one dot. This heuristic catches our two
    # compose services (production and test) plus any operator-defined
    # ones without overreaching to real hosts.
    if "." not in host:
        return {"ssl": False}
    return {}


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
    # Recycle connections that have sat idle in the pool longer than this.
    # Primary dead-connection detection is via TCP keepalives (see
    # ``_apply_tcp_keepalive``) — the OS detects a half-open socket within
    # ~25 seconds and asyncpg evicts it automatically. This max_inactive
    # lifetime is a belt-and-suspenders fallback for connections that go
    # stale in other ways (e.g. postgres idle-session-timeout, server
    # restart). Env-tunable; 0 disables (asyncpg semantics).
    try:
        max_inactive = float(os.environ.get("HERMES_PG_POOL_MAX_INACTIVE_S", "120"))
    except ValueError:
        max_inactive = 120.0
    pool = await asyncpg.create_pool(
        dsn,
        min_size=ms,
        max_size=Ms,
        command_timeout=command_timeout,
        max_inactive_connection_lifetime=max_inactive,
        init=_setup_jsonb_codec,
        **_ssl_kwarg_for_dsn(dsn),
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


def reset_pool_for_new_loop() -> None:
    """Discard the current pool synchronously, without awaiting ``close()``.

    asyncpg pools are bound to the event loop that created them. A process
    that runs its own loop via ``asyncio.run`` (e.g. ``hermes substrate
    worker run``) must not inherit the pool that a sync entry point —
    ``main()``'s ``ensure_pool_sync()`` — bound to ``_sync_loop``: every DB
    call on the new loop would raise ``got Future ... attached to a
    different loop``. ``Pool.terminate()`` is synchronous and loop-agnostic
    (it aborts the transports), so it is safe to call from the new loop's
    thread even though the stale pool belongs to ``_sync_loop``. The caller
    re-creates the pool on its own loop via ``init()``.
    """
    global _pool
    with _pool_lock:
        p, _pool = _pool, None
    if p is not None:
        p.terminate()


def pool() -> asyncpg.Pool:
    if _pool is not None:
        return _pool

    # Lazy bootstrap: if a DSN is in the environment but init() was never
    # called, initialise the pool on first use. This lets CLI subcommands
    # that don't touch the DB (e.g. ``hermes --help``, ``hermes version``)
    # run in environments without a live PG instance, while DB-touching
    # subcommands still get a working pool without an explicit init step
    # at the entry point.
    dsn = os.environ.get("HERMES_PG_DSN")
    if not dsn:
        raise RuntimeError("hermes_db.init() not called")

    # Lazy bootstrap is ONLY safe from a pure-sync context (no running event
    # loop on this thread). asyncpg pools are loop-bound: binding a fresh
    # pool to the persistent ``_sync_loop`` here while the caller is awaiting
    # on a *different* loop (an own-loop entry point like ``hermes substrate
    # worker run``, the gateway's main loop, a pytest-asyncio test) makes the
    # caller's next ``acquire()`` raise ``got Future ... attached to a
    # different loop`` — and ``_sync_loop.run_until_complete`` from inside a
    # running loop raises ``Cannot run the event loop while another loop is
    # running`` outright. Either way, a confusing crash. Convert it into a
    # clear, actionable error: async callers must ``await
    # hermes_db.init(dsn)`` on their own loop first (own-loop entry points
    # also call ``reset_pool_for_new_loop()`` to drop any inherited pool).
    try:
        asyncio.get_running_loop()
        inside_running_loop = True
    except RuntimeError:
        inside_running_loop = False
    if inside_running_loop:
        raise RuntimeError(
            "hermes_db.pool() accessed before init() from inside a running "
            "event loop. asyncpg pools are loop-bound — call `await "
            "hermes_db.init(dsn)` on this loop first. Own-loop entry points "
            "should also call hermes_db.reset_pool_for_new_loop() to drop "
            "any pool inherited from the sync bridge."
        )

    # Pure-sync context: safe to drive the init on the persistent sync loop.
    _get_sync_loop().run_until_complete(init(dsn))
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


_run_sync_local = threading.local()
_sync_loop_mutex = threading.Lock()

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
    if getattr(_run_sync_local, "in_run_sync", False):
        # Case 2: true re-entrant into our own sync loop.
        raise RuntimeError(
            "hermes_db.run_sync called from inside its own sync loop; "
            "refactor caller to `await` the coroutine directly."
        )

    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None

    loop = _get_sync_loop()

    if current is None:
        # Case 1: pure sync context, run on this thread.
        _run_sync_local.in_run_sync = True
        try:
            with _sync_loop_mutex:
                # NB: this function intentionally does NOT lazy-bootstrap
                # the pool. The eager-bootstrap path used to fire whenever
                # ``HERMES_PG_DSN`` was set, even when the coro was a Mock
                # / AsyncMock that didn't actually need a real connection.
                # Under pytest, that drove ``asyncpg.create_pool(...)`` on
                # every CLI unit test, which intermittently segfaulted on
                # GitHub Actions runners during the asyncpg protocol-class
                # init. Bootstrap is now the caller's responsibility: real
                # entry points (CLI ``main``, daemons, scripts) call
                # ``ensure_pool_sync()`` once up-front; tests use the
                # ``hermes_db_initialized*`` fixtures; mocked tests need
                # nothing.
                return loop.run_until_complete(coro)
        finally:
            _run_sync_local.in_run_sync = False

    # Case 3: a different loop is running in this thread (pytest-asyncio,
    # ACP server callback, etc.). Offload to a worker thread so Python's
    # one-loop-per-thread check doesn't reject us.
    executor = _get_sync_offload_executor()

    def _offload():
        if getattr(_run_sync_local, "in_run_sync", False):
            raise RuntimeError(
                "hermes_db.run_sync called from inside its own sync loop; "
                "refactor caller to `await` the coroutine directly."
            )
        _run_sync_local.in_run_sync = True
        try:
            with _sync_loop_mutex:
                return loop.run_until_complete(coro)
        finally:
            _run_sync_local.in_run_sync = False

    future = executor.submit(_offload)
    return future.result()


# Dedicated executor for routing cross-loop DB coroutines onto the pool's
# loop (see ``run_on_pool_loop``). Kept separate from the gateway's agent-turn
# executor so a long-running agent turn can't starve a quick handoff poll.
_route_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
_route_executor_lock = threading.Lock()


def _get_route_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _route_executor
    with _route_executor_lock:
        if _route_executor is None:
            _route_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="hermes-db-route"
            )
        return _route_executor


async def run_on_pool_loop(coro: Awaitable[T]) -> T:
    """Await a DB coroutine on the asyncpg pool's event loop.

    asyncpg connections are loop-bound: a coroutine that does
    ``async with hermes_db.connection() as conn: await conn.fetch(...)``
    can only run on the loop the pool was created on. A process that hosts
    DB access from TWO loops — e.g. the gateway runs its main loop
    ``L_gw`` via ``asyncio.run(start_gateway())`` but the pool is bound to
    ``_sync_loop`` (created by ``main()``'s ``ensure_pool_sync()`` before
    ``L_gw`` exists) — would otherwise hit
    ``ConnectionDoesNotExistError`` / ``cannot perform operation: another
    operation is in progress`` whenever an async-native caller (the handoff
    watcher, ``/title`` / ``/resume`` / ``/branch`` handlers, telegram-topic
    ops) awaits the pool from the wrong loop.

    This helper sends such a coroutine to the pool's loop:

    * Already on the pool's loop (the common ``run_sync`` hot path, or a
      single-loop process like the substrate worker): await it directly —
      no thread hop, no overhead.
    * On a different loop than the pool: drive it on the pool's loop via the
      ``run_sync`` bridge, executed in a dedicated worker thread so the
      caller's loop is never blocked.

    Only the ``_sync_loop``-bound-pool topology can be driven on demand
    (``run_sync`` runs ``_sync_loop`` via ``run_until_complete``); if the
    pool is bound to some other, non-running loop we cannot safely route, so
    we raise rather than silently issue a cross-loop operation.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    pool_loop = getattr(_pool, "_loop", None)
    if running is None or pool_loop is None or running is pool_loop:
        return await coro
    if pool_loop is not _get_sync_loop():
        raise RuntimeError(
            "run_on_pool_loop: the asyncpg pool is bound to a loop that is "
            "neither the current running loop nor the sync-bridge loop; "
            "cannot route the coroutine safely. This indicates a pool "
            "created on an unexpected loop."
        )
    return await running.run_in_executor(_get_route_executor(), run_sync, coro)


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
