"""Tests for Curator self-state emission — spec §10.4.

Every release and every alarm produces one ``substrate.self_state``
slice with the schemas in spec §7.2. Audit emissions run AFTER the
relevant transaction commits.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from substrate import Substrate
from substrate.agents import Curator
from substrate.l0 import commit_slice
from substrate.storage import Family, Modality


@pytest_asyncio.fixture
async def substrate(hermes_db_initialized):
    import hermes_db

    return Substrate.from_pool(hermes_db.pool())


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _register_profile(
    pool, name: str, *, tombstone_policy: str = "thin",
    release_after_consolidation: bool = False,
    window_seconds: int = 60,
    reinforcement_bump: float = 0.2,
    min_salience: float = 0.05,
) -> UUID:
    profile_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO substrate_decay_profiles
                (profile_id, name, natural_half_life, consolidation_window,
                 reinforcement_bump, min_salience_to_retain,
                 release_after_consolidation, pending_ttl,
                 tombstone_policy, applies_to_modality)
            VALUES
                ($1, $2, interval '1 hour', make_interval(secs => $3),
                 $4, $5, $6, interval '30 seconds',
                 $7, 'structured_event')
            """,
            profile_id, name, float(window_seconds), float(reinforcement_bump),
            float(min_salience), release_after_consolidation, tombstone_policy,
        )
    return profile_id


@pytest.mark.asyncio
async def test_release_emits_self_state_slice(substrate):
    """One release → one ``curator.release`` slice on
    ``substrate.self_state`` with all the spec §7.2 keys."""
    import hermes_db

    profile_id = await _register_profile(substrate.pool, "test-emit-rel")
    stream = await substrate.streams.register(
        name="hermes.test.emit_release",
        family=Family.SELF_STATE,
        modality=Modality.STRUCTURED_EVENT,
        source="test",
        organ="pytest",
        decay_profile_id=profile_id,
    )
    await commit_slice(
        substrate, stream.stream_id, {"x": 1}, event_time_world=_now_utc()
    )
    async with hermes_db.connection() as conn:
        slice_id = await conn.fetchval(
            """
            UPDATE substrate_slices
               SET sentinel_state='passed', trust_score=0.5,
                   pending_committed_at=NULL,
                   salience_score=0.01,
                   salience_updated_at=now() - interval '1 minute'
             WHERE slice_id = (
                 SELECT slice_id FROM substrate_slices
                  WHERE stream_id=$1 ORDER BY ingest_time_world DESC LIMIT 1
             )
            RETURNING slice_id
            """,
            stream.stream_id,
        )

    curator = Curator(substrate)
    released = await curator._evaluate_releases()
    assert len(released) == 1
    await curator._emit_release_audit(released)

    async with hermes_db.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT sl.payload
              FROM substrate_slices sl
              JOIN substrate_streams st ON st.stream_id = sl.stream_id
             WHERE st.name = 'substrate.self_state'
               AND sl.payload->>'event' = 'curator.release'
               AND sl.payload->>'slice_id' = $1
            """,
            str(slice_id),
        )
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["event"] == "curator.release"
    assert payload["slice_id"] == str(slice_id)
    assert payload["stream_id"] == str(stream.stream_id)
    assert payload["tombstone_policy"] == "thin"
    assert payload["salience_at_release"] == pytest.approx(0.01, abs=0.001)
    assert "released_at" in payload


@pytest.mark.asyncio
async def test_alarm_emits_self_state_slice(substrate):
    import hermes_db

    profile_id = await _register_profile(
        substrate.pool, "test-emit-alarm",
        window_seconds=60, reinforcement_bump=0.3,
    )
    stream = await substrate.streams.register(
        name="hermes.test.emit_alarm",
        family=Family.SELF_STATE,
        modality=Modality.STRUCTURED_EVENT,
        source="test",
        organ="pytest",
        decay_profile_id=profile_id,
    )
    await commit_slice(
        substrate, stream.stream_id, {"x": 1}, event_time_world=_now_utc()
    )
    async with hermes_db.connection() as conn:
        slice_id = await conn.fetchval(
            """
            UPDATE substrate_slices
               SET sentinel_state='passed', trust_score=0.5,
                   pending_committed_at=NULL,
                   salience_score=0.4,
                   event_time_world      = now() - interval '120 seconds',
                   perception_time_world = now() - interval '120 seconds',
                   ingest_time_world     = now() - interval '120 seconds',
                   time_start_world      = now() - interval '120 seconds',
                   time_end_world        = now() - interval '120 seconds',
                   salience_updated_at   = now() - interval '120 seconds'
             WHERE slice_id = (
                 SELECT slice_id FROM substrate_slices
                  WHERE stream_id=$1 ORDER BY ingest_time_world DESC LIMIT 1
             )
            RETURNING slice_id
            """,
            stream.stream_id,
        )

    curator = Curator(substrate)
    alarmed = await curator._alarm_pathological()
    assert len(alarmed) == 1
    await curator._emit_alarm_audit(alarmed)

    async with hermes_db.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT sl.payload
              FROM substrate_slices sl
              JOIN substrate_streams st ON st.stream_id = sl.stream_id
             WHERE st.name = 'substrate.self_state'
               AND sl.payload->>'event' = 'curator.pathological_forgetting_alarm'
               AND sl.payload->>'slice_id' = $1
            """,
            str(slice_id),
        )
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["event"] == "curator.pathological_forgetting_alarm"
    assert payload["slice_id"] == str(slice_id)
    assert payload["age_seconds"] >= 60
    assert payload["consolidation_window_seconds"] == 60
    assert payload["bumped_to"] == pytest.approx(0.7, abs=0.001)
    assert "alarmed_at" in payload


@pytest.mark.asyncio
async def test_no_emit_when_nothing_to_audit(substrate):
    """Quiet tick (nothing to release, nothing to alarm) produces zero
    ``substrate.self_state`` slices from the curator path."""
    import hermes_db

    curator = Curator(substrate)
    await curator._emit_release_audit([])
    await curator._emit_alarm_audit([])

    async with hermes_db.connection() as conn:
        n = await conn.fetchval(
            """
            SELECT COUNT(*) FROM substrate_slices sl
              JOIN substrate_streams st ON st.stream_id = sl.stream_id
             WHERE st.name = 'substrate.self_state'
               AND sl.payload->>'event' LIKE 'curator.%'
            """
        )
    assert n == 0
