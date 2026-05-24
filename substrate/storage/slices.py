"""SliceRepo — the high-volume substrate write surface.

Most methods take an explicit ``asyncpg.Connection`` so the caller
controls transactions. Sentinel batches and force-reject sweeps run
inside ``hermes_db.transaction()`` blocks; the L0 ``commit_slice``
helper passes its own ``conn`` so a Hermes hook can join the caller's
transaction (e.g. ``on_session_start`` shares a txn with the
``sessions`` INSERT in ``SessionDB.create_session``).

The ``commit`` method is here (rather than in ``substrate.l0.api``) so
test code and the Sentinel batch summary can call it directly without
duplicating the prepared INSERT. The public L0 surface lives in
:mod:`substrate.l0.api` and wraps this method with validation +
caller-friendly errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from substrate.storage.rows import _slice_from_row
from substrate.storage.types import (
    Address,
    Modality,
    SentinelState,
    Slice,
)

if TYPE_CHECKING:  # pragma: no cover
    import asyncpg


@dataclass(frozen=True)
class ReleaseRecord:
    """One released slice as returned by ``SliceRepo.release_eligible``.

    Carries the data the Curator's audit emission needs without a
    second SELECT (Phase B spec §6.3). ``salience_at_release`` is the
    salience score at the moment of release decision — useful for
    Reflector/Critic calibration on Curator's threshold choices.
    """

    slice_id: UUID
    stream_id: UUID
    tombstone_policy: str
    salience_at_release: float


class SliceRepo:
    """Reads + writes for ``substrate_slices``.

    Each method declares whether it requires an explicit ``conn`` (txn
    participation) or can acquire from the pool on its own (no txn).
    """

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    # ------------------------------------------------------------------
    # Write: commit a fresh pending slice.
    # ------------------------------------------------------------------

    async def commit(
        self,
        *,
        conn: "asyncpg.Connection",
        stream_id: UUID,
        time_start_world: datetime,
        time_end_world: datetime,
        event_time_world: datetime,
        perception_time_world: datetime,
        payload: Optional[dict],
        payload_blob_ref: Optional[str],
        payload_modality: Modality,
        metadata: dict,
        summary_of: Optional[list[dict]] = None,
    ) -> tuple[UUID, datetime]:
        """Insert a fresh slice with ``sentinel_state='pending'``.

        Always called from within a caller-supplied transaction; the
        L0 :func:`commit_slice` API acquires/opens that transaction as
        needed. Returns the freshly-allocated ``(slice_id,
        ingest_time_world)`` tuple from ``RETURNING`` so the L0
        wrapper can build an :class:`Address`.

        Notes:

        * ``payload`` is a Python ``dict`` (or ``None`` for
          binary-blob slices). The asyncpg pool's JSONB codec encodes
          dicts directly — *never use ``::jsonb`` casts* (corrupts the
          prepared-statement type cache per Phase 0 ADR).
        * ``salience_score`` defaults to 1.0 on insert; Curator will
          decay it later (Phase B+).
        * ``trust_score`` is NULL while pending; Sentinel sets it on
          decision.
        * ``pending_committed_at`` is set to ``now()`` so the
          partial-index queries (Section 5.2 of the spec) can sort by
          oldest pending.
        """
        slice_id = uuid4()
        # Clock-skew safety: cap caller-supplied timestamps to PG's
        # ``now()`` inside the INSERT so the
        # ``event <= perception <= ingest`` CHECK constraints hold even
        # when the Hermes host's wall clock drifts a few ms ahead of
        # PG's wall clock. This is the realistic case when Hermes runs
        # in a VM separate from the Postgres container.
        #
        # Formula (let E, P be caller's event_time_world,
        # perception_time_world; now() be PG's wall clock at INSERT):
        #   stored_event      = LEAST(E, now())
        #   stored_perception = LEAST(GREATEST(P, E), now())
        # Walk through:
        #   - E ≤ now, P ≤ now, P ≥ E: stored = (E, P).
        #   - E ≤ now, P > now (skew):  stored = (E, now).
        #   - E > now (skew):           stored = (now, now).
        # In every case stored_event ≤ stored_perception ≤ now (ingest),
        # so the CHECK constraints are satisfied.
        row = await conn.fetchrow(
            """
            INSERT INTO substrate_slices
                (slice_id, stream_id,
                 time_start_world, time_end_world,
                 event_time_world, perception_time_world,
                 payload, payload_blob_ref, payload_modality,
                 sentinel_state, pending_committed_at,
                 salience_score, metadata, summary_of)
            VALUES
                ($1, $2, $3, $4,
                 LEAST($5, now()),
                 LEAST(GREATEST($6, $5), now()),
                 $7, $8, $9,
                 'pending', now(), 1.0, $10, $11)
            RETURNING slice_id, ingest_time_world
            """,
            slice_id,
            stream_id,
            time_start_world,
            time_end_world,
            event_time_world,
            perception_time_world,
            payload,
            payload_blob_ref,
            payload_modality.value,
            metadata,
            summary_of,
        )
        # ``RETURNING`` always emits a row on a successful INSERT — assert
        # so a future schema change that drops the columns fails loudly
        # instead of silently returning ``None`` here.
        assert row is not None, "INSERT RETURNING produced no row"
        return row["slice_id"], row["ingest_time_world"]

    # ------------------------------------------------------------------
    # Sentinel / force-reject reads.
    # ------------------------------------------------------------------

    async def list_pending(
        self,
        conn: "asyncpg.Connection",
        *,
        limit: int = 100,
        lock: bool = False,
    ) -> list[Slice]:
        """Return oldest-pending-first slices.

        With ``lock=True``, uses ``FOR UPDATE SKIP LOCKED`` so multiple
        Sentinel workers (Phase B+ horizontal scaling) never decide the
        same row twice. ``SKIP LOCKED`` makes contended rows invisible
        to other workers rather than blocking — exactly the semantics a
        batch-tick worker wants.
        """
        sql = """
            SELECT * FROM substrate_slices
             WHERE sentinel_state = 'pending'
             ORDER BY pending_committed_at ASC NULLS LAST
             LIMIT $1
        """
        if lock:
            sql += " FOR UPDATE SKIP LOCKED"
        rows = await conn.fetch(sql, limit)
        return [_slice_from_row(r) for r in rows]

    async def get_by_id(
        self,
        conn: "asyncpg.Connection",
        slice_id: UUID,
    ) -> Optional[Slice]:
        """Return a slice by id, or ``None`` if not present.

        Note: no ``ingest_time_world`` argument — slice_id is globally
        unique (UUIDv4), so PG searches every partition. Cheap in the
        steady state because partial indexes cover the common cases;
        used mostly by tests.
        """
        row = await conn.fetchrow(
            "SELECT * FROM substrate_slices WHERE slice_id = $1",
            slice_id,
        )
        return _slice_from_row(row) if row else None

    # ------------------------------------------------------------------
    # Sentinel decisions.
    # ------------------------------------------------------------------

    async def decide(
        self,
        conn: "asyncpg.Connection",
        slice_id: UUID,
        *,
        outcome: SentinelState,
        trust_score: float,
        reason: Optional[str] = None,
    ) -> None:
        """Atomically transition a slice from PENDING to ``outcome``.

        Raises ``ValueError`` if ``outcome`` is not PASSED or
        QUARANTINED (PENDING is not a legal target — Sentinel only
        moves *away* from pending). Raises ``RuntimeError`` if the slice
        was no longer pending when the UPDATE ran (concurrent decision
        race — caller should treat as benign and skip).
        """
        if outcome not in (SentinelState.PASSED, SentinelState.QUARANTINED):
            raise ValueError(f"invalid sentinel outcome: {outcome!r}")
        tag = await conn.execute(
            """
            UPDATE substrate_slices
               SET sentinel_state       = $1,
                   trust_score          = $2,
                   sentinel_reason      = $3,
                   pending_committed_at = NULL
             WHERE slice_id = $4
               AND sentinel_state = 'pending'
            """,
            outcome.value,
            trust_score,
            reason,
            slice_id,
        )
        # asyncpg returns the command tag as a string like "UPDATE 1".
        # The last token is the affected row count.
        if not tag.endswith(" 1"):
            raise RuntimeError(
                f"slice {slice_id} not pending (UPDATE tag: {tag!r})"
            )

    async def decide_many(
        self,
        conn: "asyncpg.Connection",
        decisions: list[tuple[UUID, SentinelState, float, Optional[str]]],
    ) -> int:
        """Multi-row decide via ``executemany``. ``decisions`` is a list
        of ``(slice_id, outcome, trust_score, reason)`` tuples.

        Returns the count of decision tuples submitted (asyncpg's
        ``executemany`` doesn't return per-row affected counts; callers
        that care about misses re-fetch). Returns 0 for an empty input.
        """
        if not decisions:
            return 0
        for sid, outcome, _trust, _reason in decisions:
            if outcome not in (SentinelState.PASSED, SentinelState.QUARANTINED):
                raise ValueError(
                    f"invalid sentinel outcome for {sid}: {outcome!r}"
                )
        rows = [
            (outcome.value, trust, reason, sid)
            for (sid, outcome, trust, reason) in decisions
        ]
        await conn.executemany(
            """
            UPDATE substrate_slices
               SET sentinel_state       = $1,
                   trust_score          = $2,
                   sentinel_reason      = $3,
                   pending_committed_at = NULL
             WHERE slice_id = $4
               AND sentinel_state = 'pending'
            """,
            rows,
        )
        return len(rows)

    # ------------------------------------------------------------------
    # Force-reject: delete pending slices past their TTL.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Phase B: reinforcement.
    # ------------------------------------------------------------------

    async def reinforce(
        self,
        conn: "asyncpg.Connection",
        slice_id: UUID,
        *,
        bump: Optional[float] = None,
    ) -> None:
        """Bump salience by ``bump`` or by the profile's
        ``reinforcement_bump``. Caps at 1.0.

        Updates ``salience_updated_at`` so subsequent decay starts from
        now. Does NOT update any other field — reinforcement is
        salience-only. The bump is applied SQL-side via
        ``LEAST(1.0, salience + bump)`` so concurrent reinforces don't
        trample each other.

        Reinforcing a released slice is harmless: salience stays at 0
        (already capped) and the timestamp updates. ``consolidation_state``
        is NOT brought back from ``released`` — release is one-way.
        """
        await conn.execute(
            """
            UPDATE substrate_slices sl
               SET salience_score = LEAST(1.0,
                       sl.salience_score + COALESCE($2::real, dp.reinforcement_bump)),
                   salience_updated_at = now()
              FROM substrate_streams        st
              JOIN substrate_decay_profiles dp ON dp.profile_id = st.decay_profile_id
             WHERE sl.slice_id = $1
               AND sl.stream_id = st.stream_id
            """,
            slice_id,
            bump,
        )

    # ------------------------------------------------------------------
    # Phase B: release eligible slices per tombstone policy.
    # ------------------------------------------------------------------

    async def release_eligible(
        self,
        conn: "asyncpg.Connection",
        *,
        limit: int = 200,
    ) -> list[ReleaseRecord]:
        """Release up to ``limit`` slices whose salience has fallen
        below their profile's ``min_salience_to_retain``.

        Three SQL paths per ``tombstone_policy``:
          * ``none`` — DELETE the row entirely. Requires the profile's
            ``tombstone_none_justification`` to be set (the CHECK
            constraint from Phase A migration enforces this; we don't
            need a Python-side check).
          * ``thin`` — NULL ``payload`` + ``payload_blob_ref``, zero
            salience, mark ``consolidation_state='released'``.
          * ``full`` — NULL ``payload`` + ``payload_blob_ref``, mark
            ``consolidation_state='released'`` (keep salience at its
            release-time value).

        The eligibility CTE uses ``FOR UPDATE OF sl SKIP LOCKED`` so
        concurrent Curators don't fight over the same rows.

        Returns ``ReleaseRecord`` per released slice so the caller can
        emit per-release audit slices without a second SELECT.
        """
        eligible = await conn.fetch(
            """
            SELECT sl.slice_id, sl.ingest_time_world, st.stream_id,
                   dp.tombstone_policy, sl.salience_score
              FROM substrate_slices         sl
              JOIN substrate_streams        st ON st.stream_id  = sl.stream_id
              JOIN substrate_decay_profiles dp ON dp.profile_id = st.decay_profile_id
             WHERE sl.sentinel_state      = 'passed'
               AND sl.consolidation_state <> 'released'
               AND sl.salience_score      < dp.min_salience_to_retain
               AND (NOT dp.release_after_consolidation
                    OR sl.consolidation_state = 'consolidated')
             ORDER BY sl.salience_score ASC
             LIMIT $1
             FOR UPDATE OF sl SKIP LOCKED
            """,
            limit,
        )

        released: list[ReleaseRecord] = []
        for r in eligible:
            policy = r["tombstone_policy"]
            slice_id = r["slice_id"]
            ingest = r["ingest_time_world"]
            if policy == "none":
                await conn.execute(
                    """
                    DELETE FROM substrate_slices
                     WHERE slice_id = $1 AND ingest_time_world = $2
                    """,
                    slice_id,
                    ingest,
                )
            elif policy == "thin":
                await conn.execute(
                    """
                    UPDATE substrate_slices
                       SET payload = NULL,
                           payload_blob_ref = NULL,
                           salience_score = 0,
                           consolidation_state = 'released'
                     WHERE slice_id = $1 AND ingest_time_world = $2
                    """,
                    slice_id,
                    ingest,
                )
            elif policy == "full":
                await conn.execute(
                    """
                    UPDATE substrate_slices
                       SET payload = NULL,
                           payload_blob_ref = NULL,
                           consolidation_state = 'released'
                     WHERE slice_id = $1 AND ingest_time_world = $2
                    """,
                    slice_id,
                    ingest,
                )
            else:  # pragma: no cover — CHECK constraint guards this
                raise ValueError(f"unknown tombstone_policy: {policy!r}")
            released.append(
                ReleaseRecord(
                    slice_id=slice_id,
                    stream_id=r["stream_id"],
                    tombstone_policy=policy,
                    salience_at_release=float(r["salience_score"]),
                )
            )
        return released

    # ------------------------------------------------------------------
    # Phase B: salience pressure read (for inspect CLI).
    # ------------------------------------------------------------------

    async def salience_pressure(
        self,
        conn: "asyncpg.Connection",
        *,
        window_seconds: int = 300,
    ) -> list[dict]:
        """Per-stream salience density + update rate over ``window_seconds``.

        Returns one row per active stream:
          * ``name`` — stream name
          * ``density`` — mean salience across non-released slices
          * ``count`` — number of non-released slices
          * ``update_rate`` — count of salience_updated_at writes in the
             last ``window_seconds`` (proxy for Curator + reinforcement
             activity)

        Surfaced by ``hermes substrate inspect curator pressure``. Phase
        B doesn't consume this programmatically — it's an operator
        observability window into what Phase F's real Conductor will
        eventually read for opportunity-forecast inputs.
        """
        rows = await conn.fetch(
            """
            SELECT st.name AS name,
                   COALESCE(AVG(sl.salience_score), 0)::real AS density,
                   COUNT(sl.slice_id)::int AS count,
                   COUNT(*) FILTER (
                       WHERE sl.salience_updated_at > now() - make_interval(secs => $1)
                   )::int AS update_rate
              FROM substrate_streams st
              LEFT JOIN substrate_slices sl
                     ON sl.stream_id = st.stream_id
                    AND sl.consolidation_state <> 'released'
             WHERE st.lifecycle_state = 'active'
             GROUP BY st.name
             ORDER BY st.name
            """,
            window_seconds,
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Force-reject: delete pending slices past their TTL.
    # ------------------------------------------------------------------

    async def force_reject_expired(
        self,
        conn: "asyncpg.Connection",
        *,
        limit: int = 100,
    ) -> list[Slice]:
        """Delete pending slices that have been pending longer than
        their stream's decay-profile ``pending_ttl``.

        Returns the deleted slices (via ``DELETE … RETURNING``) so the
        force-reject worker can emit an audit slice for each. The
        partition-key columns (``slice_id``, ``ingest_time_world``) are
        in the DELETE predicate so PG prunes to a single partition per
        matched row.

        Uses ``FOR UPDATE OF sl SKIP LOCKED`` in the CTE so concurrent
        Sentinel workers don't lose rows to a race.
        """
        rows = await conn.fetch(
            """
            WITH expired AS (
                SELECT sl.slice_id, sl.ingest_time_world
                  FROM substrate_slices         sl
                  JOIN substrate_streams        st ON st.stream_id   = sl.stream_id
                  JOIN substrate_decay_profiles dp ON dp.profile_id  = st.decay_profile_id
                 WHERE sl.sentinel_state = 'pending'
                   AND sl.pending_committed_at + dp.pending_ttl < now()
                 ORDER BY sl.pending_committed_at ASC
                 LIMIT $1
                 FOR UPDATE OF sl SKIP LOCKED
            )
            DELETE FROM substrate_slices sl
             USING expired e
             WHERE sl.slice_id          = e.slice_id
               AND sl.ingest_time_world = e.ingest_time_world
            RETURNING sl.*
            """,
            limit,
        )
        return [_slice_from_row(r) for r in rows]
