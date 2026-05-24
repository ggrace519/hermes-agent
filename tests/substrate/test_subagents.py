"""Tests for the four Phase A sub-agent stubs:

* StubSentinel — passes every pending slice with a modality-derived
  trust score; emits batch-summary audit on substrate.self_state.
* ForceRejectWorker — deletes slices past their stream's pending_ttl;
  emits force_reject_ttl audit per dropped slice.
* PartitionMaintenanceWorker — ensures rolling-window monthly
  partitions; fixed-cadence 24h tick regardless of intensity.
* StubConductor — pure data holder, per-agent intensity levels.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

from substrate import Substrate
from substrate.agents import (
    ForceRejectWorker,
    Level,
    PartitionMaintenanceWorker,
    StubConductor,
    StubSentinel,
    _trust_for_modality,
)
from substrate.l0 import commit_slice
from substrate.storage import (
    DEFAULT_STRUCTURED_PROFILE,
    DEFAULT_TEXT_PROFILE,
    Family,
    Modality,
    SentinelState,
)


@pytest_asyncio.fixture
async def substrate(hermes_db_initialized):
    import hermes_db

    return Substrate.from_pool(hermes_db.pool())


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# StubSentinel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sentinel_transitions_pending_to_passed(substrate):
    """One tick passes every pending slice with a non-null trust
    score."""
    import hermes_db

    stream = await substrate.streams.register(
        name="hermes.test.sentinel_pass",
        family=Family.SELF_STATE,
        modality=Modality.STRUCTURED_EVENT,
        source="test",
        organ="pytest",
        decay_profile_id=DEFAULT_STRUCTURED_PROFILE,
    )
    for i in range(3):
        await commit_slice(
            substrate,
            stream.stream_id,
            {"i": i},
            event_time_world=_now_utc(),
        )

    sentinel = StubSentinel(substrate)
    await sentinel.tick()

    async with hermes_db.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT sentinel_state, trust_score, pending_committed_at
              FROM substrate_slices
             WHERE stream_id = $1
            """,
            stream.stream_id,
        )
    assert len(rows) == 3
    for r in rows:
        assert r["sentinel_state"] == "passed"
        assert r["trust_score"] is not None
        # Trust score for structured_event = 0.7.
        assert r["trust_score"] == pytest.approx(0.7)
        # Decision clears the pending timestamp.
        assert r["pending_committed_at"] is None


@pytest.mark.asyncio
async def test_sentinel_emits_batch_summary(substrate):
    """After the batch tick, the substrate.self_state stream gets a
    summary slice listing the decided slice IDs."""
    import hermes_db

    stream = await substrate.streams.register(
        name="hermes.test.sentinel_audit",
        family=Family.SELF_STATE,
        modality=Modality.STRUCTURED_EVENT,
        source="test",
        organ="pytest",
        decay_profile_id=DEFAULT_STRUCTURED_PROFILE,
    )
    await commit_slice(
        substrate, stream.stream_id, {"x": 1}, event_time_world=_now_utc()
    )

    sentinel = StubSentinel(substrate)
    await sentinel.tick()

    self_state = await substrate.streams.get_by_name("substrate.self_state")
    # No ``::jsonb`` cast — pass the filter dict as a bind parameter so
    # the pool's JSONB codec handles encode. The Phase 0 ADR explicitly
    # bans ``::jsonb`` casts because they corrupt asyncpg's statement
    # type cache.
    async with hermes_db.connection() as conn:
        audit = await conn.fetchrow(
            """
            SELECT payload, metadata
              FROM substrate_slices
             WHERE stream_id = $1
               AND payload @> $2
             ORDER BY ingest_time_world DESC
             LIMIT 1
            """,
            self_state.stream_id,
            {"event": "sentinel_batch_decision"},
        )
    assert audit is not None
    payload = audit["payload"]
    assert payload["event"] == "sentinel_batch_decision"
    assert payload["count"] >= 1
    assert isinstance(payload["slice_ids"], list)
    assert audit["metadata"]["agent"] == "sentinel"


@pytest.mark.asyncio
async def test_sentinel_empty_pending_queue_is_noop(substrate):
    """A tick with no pending slices does not emit an audit slice."""
    import hermes_db

    self_state = await substrate.streams.get_by_name("substrate.self_state")
    async with hermes_db.connection() as conn:
        before = await conn.fetchval(
            "SELECT count(*) FROM substrate_slices WHERE stream_id = $1",
            self_state.stream_id,
        )

    sentinel = StubSentinel(substrate)
    await sentinel.tick()

    async with hermes_db.connection() as conn:
        after = await conn.fetchval(
            "SELECT count(*) FROM substrate_slices WHERE stream_id = $1",
            self_state.stream_id,
        )
    assert after == before


@pytest.mark.asyncio
async def test_sentinel_concurrent_ticks_no_double_decide(substrate):
    """Two concurrent Sentinel ticks against the same pending queue
    must not double-decide rows. SKIP LOCKED on ``list_pending`` is
    the contract that makes this safe.
    """
    import hermes_db

    stream = await substrate.streams.register(
        name="hermes.test.sentinel_concurrent",
        family=Family.SELF_STATE,
        modality=Modality.STRUCTURED_EVENT,
        source="test",
        organ="pytest",
        decay_profile_id=DEFAULT_STRUCTURED_PROFILE,
    )
    for i in range(10):
        await commit_slice(
            substrate, stream.stream_id, {"i": i}, event_time_world=_now_utc()
        )

    s1 = StubSentinel(substrate)
    s2 = StubSentinel(substrate)
    await asyncio.gather(s1.tick(), s2.tick())

    async with hermes_db.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT sentinel_state, trust_score
              FROM substrate_slices
             WHERE stream_id = $1
            """,
            stream.stream_id,
        )
    # All 10 slices passed exactly once (decide_many UPDATE is
    # idempotent on the WHERE sentinel_state='pending' guard).
    assert len(rows) == 10
    for r in rows:
        assert r["sentinel_state"] == "passed"
        assert r["trust_score"] is not None


def test_trust_for_modality():
    """Pure-Python sanity check on the modality → trust mapping."""
    assert _trust_for_modality(Modality.TEXT) == 0.5
    assert _trust_for_modality(Modality.STRUCTURED_EVENT) == 0.7
    assert _trust_for_modality(Modality.SIGNAL) == 0.3


# ---------------------------------------------------------------------------
# ForceRejectWorker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_reject_deletes_expired_pending(substrate):
    """Slice past TTL is force-rejected + audit emitted."""
    import hermes_db

    stream = await substrate.streams.register(
        name="hermes.test.force_reject",
        family=Family.SELF_STATE,
        modality=Modality.STRUCTURED_EVENT,
        source="test",
        organ="pytest",
        decay_profile_id=DEFAULT_STRUCTURED_PROFILE,  # 15s pending_ttl
    )
    address = await commit_slice(
        substrate, stream.stream_id, {"doomed": True}, event_time_world=_now_utc()
    )
    # Backdate so the TTL has expired without waiting.
    async with hermes_db.connection() as conn:
        slice_id = await conn.fetchval(
            "SELECT slice_id FROM substrate_slices WHERE stream_id = $1",
            stream.stream_id,
        )
        await conn.execute(
            """
            UPDATE substrate_slices
               SET pending_committed_at = now() - INTERVAL '1 minute'
             WHERE slice_id = $1
            """,
            slice_id,
        )

    worker = ForceRejectWorker(substrate)
    await worker.tick()

    async with hermes_db.connection() as conn:
        gone = await conn.fetchval(
            "SELECT count(*) FROM substrate_slices WHERE slice_id = $1",
            slice_id,
        )
    assert gone == 0  # row deleted

    # Audit emitted on substrate.self_state. JSONB filter via param,
    # not ``::jsonb`` cast (Phase 0 ADR).
    self_state = await substrate.streams.get_by_name("substrate.self_state")
    async with hermes_db.connection() as conn:
        audit = await conn.fetchrow(
            """
            SELECT payload, metadata
              FROM substrate_slices
             WHERE stream_id = $1
               AND payload @> $2
             ORDER BY ingest_time_world DESC
             LIMIT 1
            """,
            self_state.stream_id,
            {"event": "force_reject_ttl"},
        )
    assert audit is not None
    assert audit["payload"]["slice_id"] == str(slice_id)
    assert audit["metadata"]["reason"] == "force_reject_ttl"


@pytest.mark.asyncio
async def test_force_reject_leaves_unexpired_pending(substrate):
    """Slice within TTL is NOT touched."""
    import hermes_db

    stream = await substrate.streams.register(
        name="hermes.test.force_reject_healthy",
        family=Family.SELF_STATE,
        modality=Modality.STRUCTURED_EVENT,
        source="test",
        organ="pytest",
        decay_profile_id=DEFAULT_STRUCTURED_PROFILE,
    )
    await commit_slice(
        substrate, stream.stream_id, {"alive": True}, event_time_world=_now_utc()
    )

    worker = ForceRejectWorker(substrate)
    await worker.tick()

    async with hermes_db.connection() as conn:
        survives = await conn.fetchval(
            """
            SELECT sentinel_state FROM substrate_slices WHERE stream_id = $1
            """,
            stream.stream_id,
        )
    assert survives == "pending"


@pytest.mark.asyncio
async def test_force_reject_empty_queue_no_audit(substrate):
    """No expired rows → no audit slices emitted."""
    import hermes_db

    self_state = await substrate.streams.get_by_name("substrate.self_state")
    async with hermes_db.connection() as conn:
        before = await conn.fetchval(
            "SELECT count(*) FROM substrate_slices WHERE stream_id = $1",
            self_state.stream_id,
        )
    worker = ForceRejectWorker(substrate)
    await worker.tick()
    async with hermes_db.connection() as conn:
        after = await conn.fetchval(
            "SELECT count(*) FROM substrate_slices WHERE stream_id = $1",
            self_state.stream_id,
        )
    assert after == before


# ---------------------------------------------------------------------------
# PartitionMaintenanceWorker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partition_maintenance_creates_ahead_window(substrate):
    """One tick creates partitions for current month + 2 ahead."""
    import hermes_db
    from substrate.storage.partitions import list_existing_partitions

    worker = PartitionMaintenanceWorker(substrate, ahead_months=2)
    await worker.tick()

    async with hermes_db.connection() as conn:
        names = await list_existing_partitions(conn)

    # Default partition + at least 3 month partitions present.
    assert "substrate_slices_default" in names
    month_partitions = [n for n in names if n != "substrate_slices_default"]
    assert len(month_partitions) >= 3


def test_partition_maintenance_interval_is_daily_regardless_of_level():
    """The static ``_interval_for`` override returns 24h for every
    non-OFF level."""
    assert PartitionMaintenanceWorker._interval_for(Level.LOW) == 86400.0
    assert PartitionMaintenanceWorker._interval_for(Level.HIGH) == 86400.0
    assert PartitionMaintenanceWorker._interval_for(Level.FULL) == 86400.0
    # OFF still halts the worker.
    assert PartitionMaintenanceWorker._interval_for(Level.OFF) is None


# ---------------------------------------------------------------------------
# StubConductor
# ---------------------------------------------------------------------------


class TestStubConductor:
    def test_intensity_for_unset_agent_defaults_to_low(self):
        # substrate=None — Conductor doesn't actually touch the
        # substrate facade until Phase B+.
        conductor = StubConductor(substrate=None)
        assert conductor.intensity_for("anyone") is Level.LOW

    def test_intensity_for_sentinel_defaults_to_full(self):
        conductor = StubConductor(substrate=None)
        assert conductor.intensity_for("sentinel", is_sentinel=True) is Level.FULL

    def test_set_and_get_intensity_round_trips(self):
        conductor = StubConductor(substrate=None)
        conductor.set_intensity("force-reject", Level.HIGH)
        assert conductor.intensity_for("force-reject") is Level.HIGH

    def test_snapshot_returns_copy(self):
        conductor = StubConductor(substrate=None)
        conductor.set_intensity("a", Level.MODERATE)
        conductor.set_intensity("b", Level.HIGH)
        snap = conductor.snapshot()
        assert snap == {"a": Level.MODERATE, "b": Level.HIGH}
        # Mutating the snapshot does not affect the conductor's state.
        snap["c"] = Level.FULL
        assert "c" not in conductor.snapshot()
