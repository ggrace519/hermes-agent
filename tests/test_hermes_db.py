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


def test_pool_raises_before_init():
    # Reset module state for this synchronous test.
    hermes_db._pool = None
    with pytest.raises(RuntimeError, match="hermes_db.init"):
        hermes_db.pool()


def test_run_sync_executes_coroutine_synchronously():
    async def make_seven():
        return 7
    assert hermes_db.run_sync(make_seven()) == 7


@pytest.mark.asyncio
async def test_jsonb_codec_returns_python_objects(initialized_db):
    async with hermes_db.connection() as conn:
        result = await conn.fetchval("SELECT '{\"a\":1,\"b\":[2,3]}'::jsonb")
    assert result == {"a": 1, "b": [2, 3]}
