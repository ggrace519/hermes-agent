import os
import pytest
import pytest_asyncio
import hermes_db


@pytest_asyncio.fixture
async def initialized_db(hermes_db_dsn):
    """`hermes_db_dsn` fixture comes from tests/conftest.py (Task 6).

    For Task 5's red phase the fixture does not exist yet; this test will
    fail at fixture collection. That's expected — Task 6 makes it pass.

    Defensive loop-binding check mirrors ``hermes_db_initialized`` in
    conftest: a prior sync test (or a ``run_sync`` call that lazy-
    bootstrapped the pool on ``hermes_db._sync_loop``) may have left
    ``hermes_db._pool`` bound to a different loop. ``init()`` is
    idempotent — it would silently return without rebinding — so the
    test body would inherit the stale pool and explode on teardown
    with a cross-loop error from ``PoolConnectionHolder.close()``.
    """
    import asyncio
    if hermes_db._pool is not None:
        current = asyncio.get_running_loop()
        pool_loop = getattr(hermes_db._pool, "_loop", None)
        if pool_loop is not current:
            try:
                hermes_db.run_sync(hermes_db.close())
            except Exception:
                hermes_db._pool = None
    await hermes_db.init(hermes_db_dsn)
    yield
    await hermes_db.close()


@pytest.mark.asyncio
async def test_pool_acquires_a_working_connection(initialized_db):
    async with hermes_db.connection() as conn:
        result = await conn.fetchval("SELECT 1")
    assert result == 1


@pytest.mark.asyncio
async def test_pool_is_singleton(initialized_db):
    p1 = hermes_db.pool()
    p2 = hermes_db.pool()
    assert p1 is p2


@pytest.mark.asyncio
async def test_transaction_commits_on_success(initialized_db):
    async with hermes_db.transaction() as conn:
        await conn.execute("CREATE TEMP TABLE t (x INT)")
        await conn.execute("INSERT INTO t VALUES (1)")
        n = await conn.fetchval("SELECT count(*) FROM t")
    assert n == 1


@pytest.mark.asyncio
async def test_transaction_rolls_back_on_exception(initialized_db):
    with pytest.raises(RuntimeError):
        async with hermes_db.transaction() as conn:
            await conn.execute("CREATE TEMP TABLE u (x INT) ON COMMIT DROP")
            await conn.execute("INSERT INTO u VALUES (1)")
            raise RuntimeError("boom")
    # Temp table is dropped at txn rollback; verify no leak in this connection.
    async with hermes_db.connection() as conn:
        exists = await conn.fetchval(
            "SELECT to_regclass('pg_temp.u') IS NOT NULL"
        )
    assert exists is False


def test_pool_raises_before_init(monkeypatch):
    # Reset module state for this synchronous test.
    hermes_db._pool = None
    # ``pool()`` now lazy-initialises from ``HERMES_PG_DSN`` when present, so
    # to assert the "init not called" RuntimeError we must also ensure no
    # DSN is in the environment (otherwise lazy init kicks in and returns a
    # real pool).
    monkeypatch.delenv("HERMES_PG_DSN", raising=False)
    with pytest.raises(RuntimeError, match="hermes_db.init"):
        hermes_db.pool()


@pytest.mark.asyncio
async def test_pool_refuses_lazy_bootstrap_inside_running_loop(monkeypatch):
    """From inside a running event loop, ``pool()`` must NOT lazy-bind a new
    pool to the persistent sync loop (the cross-loop footgun). It raises a
    clear, actionable error instead so an own-loop entry point that forgot
    to ``await init()`` gets a useful message, not a baffling
    ``got Future ... attached to a different loop`` later.
    """
    saved_pool = hermes_db._pool
    hermes_db._pool = None
    # A DSN is present (so the old code would have attempted lazy init);
    # the guard fires before any connection is opened, so the bogus host
    # is never contacted.
    monkeypatch.setenv("HERMES_PG_DSN", "postgresql://u:p@localhost:5432/db")
    try:
        with pytest.raises(RuntimeError, match="running event loop"):
            hermes_db.pool()
    finally:
        hermes_db._pool = saved_pool


def test_ensure_pool_sync_returns_false_when_no_dsn(monkeypatch):
    """When no DSN is configured, ``ensure_pool_sync`` is a no-op that
    returns False — sync entry points use this to gracefully degrade.
    """
    monkeypatch.delenv("HERMES_PG_DSN", raising=False)
    # Don't close an existing pool (would interfere with later tests
    # that share the module-level singleton); just verify the no-DSN
    # branch returns False when called against an uninitialised state.
    saved_pool = hermes_db._pool
    hermes_db._pool = None
    try:
        assert hermes_db.ensure_pool_sync() is False
        assert hermes_db._pool is None
    finally:
        hermes_db._pool = saved_pool


def test_run_sync_executes_coroutine_synchronously():
    async def make_seven():
        return 7
    assert hermes_db.run_sync(make_seven()) == 7


def test_ssl_kwarg_skips_ssl_for_local_dsns():
    """Local DSNs (docker-compose PG via localhost/127.0.0.1/[::1] or
    any single-label compose service hostname) get ``ssl=False`` so
    asyncpg skips its SSL upgrade negotiation. The negotiation runs
    ``_create_ssl_connection`` even for ``sslmode=prefer``-then-
    downgrade and intermittently segfaults inside CPython's ssl module
    on GitHub Actions runners — taking pytest workers down with no
    Python traceback. Local compose PG has no SSL anyway; this is just
    skipping a no-op handshake."""
    for dsn in (
        "postgresql://hermes:hermes@localhost:5432/hermes",
        "postgresql://hermes:hermes@127.0.0.1:5433/hermes",
        "postgresql://hermes:hermes@[::1]:5432/hermes",
        "postgresql://hermes:hermes@postgres:5432/hermes",
        "postgresql://hermes:hermes@postgres-test:5432/hermes",
        "postgresql://hermes:hermes@db:5432/myapp",
    ):
        assert hermes_db._ssl_kwarg_for_dsn(dsn) == {"ssl": False}, dsn


def test_ssl_kwarg_respects_explicit_sslmode_in_dsn():
    """Operators who set ``sslmode=`` in their DSN have made an explicit
    choice — never override it. Without the explicit-sslmode bail, our
    local-host heuristic could downgrade a deliberately-configured TLS
    connection."""
    dsn = "postgresql://hermes:hermes@localhost:5432/hermes?sslmode=require"
    assert hermes_db._ssl_kwarg_for_dsn(dsn) == {}


def test_ssl_kwarg_lets_asyncpg_negotiate_remote_dsns():
    """Remote DSNs (anything outside the docker-compose host aliases)
    pass through unmodified so asyncpg's default sslmode=prefer negotiates
    with the remote cluster — which is the correct behaviour for Neon /
    Supabase / RDS / any production Postgres."""
    for dsn in (
        "postgresql://user:pw@db.neon.tech:5432/proddb",
        "postgresql://user:pw@some-rds.amazonaws.com:5432/db",
        "postgresql://user:pw@10.0.0.5:5432/db",
    ):
        assert hermes_db._ssl_kwarg_for_dsn(dsn) == {}, dsn


@pytest.mark.asyncio
async def test_jsonb_codec_returns_python_objects(initialized_db):
    # Per DECISIONS.md "Phase 0 delivered" ADR: never use ``::jsonb`` casts
    # — they corrupt asyncpg's statement type cache. Use a real jsonb-typed
    # column so the codec drives both encode (dict → jsonb) and decode
    # (jsonb → dict) paths through a parameterised binding.
    payload = {"a": 1, "b": [2, 3]}
    async with hermes_db.connection() as conn:
        await conn.execute("CREATE TEMP TABLE _jsonb_codec_probe (data jsonb)")
        try:
            await conn.execute(
                "INSERT INTO _jsonb_codec_probe (data) VALUES ($1)",
                payload,
            )
            result = await conn.fetchval("SELECT data FROM _jsonb_codec_probe")
        finally:
            await conn.execute("DROP TABLE _jsonb_codec_probe")
    assert result == payload


class TestPoolMaxInactiveLifetime:
    """The pool recycles idle connections via ``max_inactive_connection_lifetime``
    (env-tunable ``HERMES_PG_POOL_MAX_INACTIVE_S``) so connections severed while
    idle — laptop suspend/resume, NAT idle-kill, a brief DB/network blip — get
    dropped within ~2 min instead of lingering ~5 min and spamming
    ``ConnectionDoesNotExistError`` each time a poller re-grabs a dead one."""

    @pytest.fixture(autouse=True)
    def _reset_pool(self):
        # init() is idempotent on a live _pool; clear it so each test exercises
        # create_pool, and never leak a mock pool to later tests.
        saved = hermes_db._pool
        hermes_db._pool = None
        yield
        hermes_db._pool = saved if saved is not None else None
        if saved is None:
            hermes_db._pool = None

    async def _capture_create_pool_kwargs(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        with patch("asyncpg.create_pool", AsyncMock(return_value=MagicMock())) as cp:
            await hermes_db.init("postgresql://u:p@localhost:5432/db")
        hermes_db._pool = None  # drop the mock pool
        return cp.await_args.kwargs

    @pytest.mark.asyncio
    async def test_default_is_120s(self, monkeypatch):
        monkeypatch.delenv("HERMES_PG_POOL_MAX_INACTIVE_S", raising=False)
        kwargs = await self._capture_create_pool_kwargs()
        assert kwargs["max_inactive_connection_lifetime"] == 120.0

    @pytest.mark.asyncio
    async def test_env_override(self, monkeypatch):
        monkeypatch.setenv("HERMES_PG_POOL_MAX_INACTIVE_S", "30")
        kwargs = await self._capture_create_pool_kwargs()
        assert kwargs["max_inactive_connection_lifetime"] == 30.0

    @pytest.mark.asyncio
    async def test_env_zero_disables(self, monkeypatch):
        monkeypatch.setenv("HERMES_PG_POOL_MAX_INACTIVE_S", "0")
        kwargs = await self._capture_create_pool_kwargs()
        assert kwargs["max_inactive_connection_lifetime"] == 0.0

    @pytest.mark.asyncio
    async def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("HERMES_PG_POOL_MAX_INACTIVE_S", "not-a-number")
        kwargs = await self._capture_create_pool_kwargs()
        assert kwargs["max_inactive_connection_lifetime"] == 120.0
