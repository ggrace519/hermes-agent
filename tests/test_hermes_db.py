import os
import pytest
import pytest_asyncio
import hermes_db


@pytest_asyncio.fixture
async def initialized_db(hermes_db_dsn):
    """`hermes_db_dsn` fixture comes from tests/conftest.py (Task 6).

    For Task 5's red phase the fixture does not exist yet; this test will
    fail at fixture collection. That's expected — Task 6 makes it pass."""
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
