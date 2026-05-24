"""ACP-suite-local fixtures.

The ACP session tests were written against SQLite SessionDB instances backed
by a per-test ``tmp_path/state.db`` file — each test started with a clean
database. After the PostgreSQL migration the SessionManager talks to the
shared ``hermes_db`` pool, so without explicit cleanup row-state from one
test leaks into the next (and breaks every assertion of the form
``assert manager.list_sessions() == []``).

This conftest restores the per-test clean-slate invariant by truncating
the session-shaped tables before and after each test. The pool itself is
left in place so we don't pay the connect / Alembic-replay cost per test.
"""

from __future__ import annotations

import os

import pytest


def _truncate_session_tables() -> None:
    """Drop all rows from session-related tables in the configured DB."""
    if not os.environ.get("HERMES_PG_DSN"):
        return

    import hermes_db

    # Bootstrap the pool first. This fixture runs between tests (outside
    # any event loop) so ``ensure_pool_sync`` can drive the bootstrap on
    # the persistent ``_sync_loop`` cleanly; without this, the inner
    # ``run_sync(_truncate())`` would hit ``pool()`` from inside its own
    # loop and have no way to initialise.
    if not hermes_db.ensure_pool_sync():
        return

    async def _truncate() -> None:
        async with hermes_db.connection() as conn:
            # CASCADE wipes dependent rows (messages, telegram_dm_*); we
            # restart identity sequences too so id-comparisons stay stable.
            await conn.execute(
                "TRUNCATE TABLE sessions RESTART IDENTITY CASCADE"
            )

    try:
        hermes_db.run_sync(_truncate())
    except Exception:
        # If the DB doesn't have the expected tables (e.g. the test
        # environment isn't fully migrated), silently no-op so we don't
        # mask the real test failure with a fixture error.
        pass


@pytest.fixture(autouse=True)
def _isolate_acp_session_db():
    """Per-test cleanup: wipe sessions/messages before and after each test."""
    _truncate_session_tables()
    yield
    _truncate_session_tables()
