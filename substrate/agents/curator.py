"""Phase B Curator — continuous decay, release, self-state emission.

Replaces the Phase A absence. The Curator manages the substrate's
salience landscape: slices fade exponentially per their decay profile's
half-life, get released below their ``min_salience_to_retain`` threshold
per the profile's tombstone policy, and emit per-decision self-state
slices so future Reflector/Critic (Phase E) develop calibration about
Curator behaviour.

The three sub-tasks per tick (decay → release → alarm) run in their own
transactions so a partial failure leaves the others intact. Audit
emissions run **after** the relevant transaction commits — a slow audit
emit doesn't extend the lock window on ``substrate_slices``.

**Phase C extension** (spec §5.7): the Curator also emits embeddings
for unembedded passed slices once per cycle. This is the backfill path
that keeps ``substrate_slices.embedding`` coverage climbing toward
100% — recall against missing-embedding slices falls back to keyword
Jaccard, so embedding-emit is an eventually-consistent optimisation,
not a correctness gate. Failures (API down, mis-encoded payload) are
logged and the slice retried up to ``RECALL_EMBEDDING_BACKFILL_MAX_RETRIES``
times before being persistently marked failed.

See [Phase B spec](https://github.com/ggrace519/llm-cognitive-thought/blob/main/docs/superpowers/specs/2026-05-25-phase-b-curator.md)
§4 (Curator's loop), §6 (release), §7 (self-state emission), and
[Phase C spec](https://github.com/ggrace519/llm-cognitive-thought/blob/main/docs/superpowers/specs/2026-05-25-phase-c-recall.md)
§5.7 (embedding-emit pipeline).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from substrate.agents.base import Level, SubAgent
# Module-level import so tests can monkeypatch
# substrate.agents.curator.embed for the embedding-emit failure cases.
from substrate.recall.embeddings import embed

if TYPE_CHECKING:  # pragma: no cover
    from substrate.facade import Substrate
    from substrate.storage.slices import ReleaseRecord


# Per-tick limits — keep ticks short so other sub-agents see a fair
# share of pool connections. Numbers from Phase B spec §4.1.
_DECAY_MIN_INTERVAL_SECONDS = 1.0
_RELEASE_BATCH_LIMIT = 200
_ALARM_BATCH_LIMIT = 100


class Curator(SubAgent):
    """Real Phase B Curator. Tick body runs decay → release → alarm.

    Floor intensity = LOW (not FULL — Curator is not a Sentinel-class
    primitive). Operator can dial OFF to disable; intensity between OFF
    and LOW (no such enum value today, but future enum extensions are
    forward-compatible) is silently demoted to LOW.
    """

    name = "curator"
    is_sentinel = False

    DECAY_MIN_INTERVAL_SECONDS = _DECAY_MIN_INTERVAL_SECONDS
    RELEASE_BATCH_LIMIT = _RELEASE_BATCH_LIMIT
    ALARM_BATCH_LIMIT = _ALARM_BATCH_LIMIT

    def __init__(self, substrate: "Substrate") -> None:
        super().__init__(substrate)
        # Pin floor at LOW. Base class default for non-sentinel is also
        # LOW; the assertive assignment here is forward-defensive against
        # future base-class default changes.
        self._level = Level.LOW
        # Phase C: per-slice retry counter for the embedding-emit loop.
        # In-process dict keyed by slice_id; bounded only by the
        # max-retries cap (failed slices get persisted into metadata
        # and then dropped from list_unembedded, so the dict naturally
        # caps).
        self._embed_failure_counts: dict[UUID, int] = {}
        # Track wall-clock of the last embedding backfill cycle so we
        # respect RECALL_EMBEDDING_BACKFILL_INTERVAL_S regardless of
        # how fast the Curator's main tick cadence is.
        self._last_embed_backfill_at: float = 0.0

    # ------------------------------------------------------------------
    # Intensity floor — Phase B spec §8.3.
    # ------------------------------------------------------------------

    def set_intensity(self, level: Level) -> None:
        """Curator-specific floor: anything strictly between OFF and LOW
        is demoted to LOW. OFF is honoured verbatim (operator opt-out).

        The OFF→LOW gap is deliberate: OFF is a deliberate operator
        gesture ("halt this sub-agent"); LOW is "minimum useful work".
        A bug-y caller passing MODERATE-1 (no such enum value today)
        should get LOW, not OFF.
        """
        # Future-proofing: today the enum has OFF, LOW, MODERATE, HIGH,
        # FULL with no values between OFF and LOW. The demotion below is
        # a no-op for those five values; it only kicks in if the enum
        # ever grows a new value between OFF and LOW.
        if level is not Level.OFF and self._level_rank(level) < self._level_rank(Level.LOW):
            self._log.debug(
                "curator: demoting set_intensity(%s) to LOW (floor)",
                level.value,
            )
            level = Level.LOW
        self._level = level

    @staticmethod
    def _level_rank(level: Level) -> int:
        """Rank levels so the floor comparison is well-defined.
        OFF=0, LOW=1, MODERATE=2, HIGH=3, FULL=4. Anything new in the
        enum landing between OFF and LOW gets a rank between 0 and 1 —
        triggering the floor.
        """
        return {
            Level.OFF: 0,
            Level.LOW: 1,
            Level.MODERATE: 2,
            Level.HIGH: 3,
            Level.FULL: 4,
        }.get(level, 1)

    # ------------------------------------------------------------------
    # Tick body — decay → release → alarm. Each sub-task is its own
    # transaction (Phase B spec §4.3). Audit emissions run AFTER commit.
    # ------------------------------------------------------------------

    async def tick(self) -> None:
        await self._apply_natural_decay()
        released = await self._evaluate_releases()
        await self._emit_release_audit(released)
        alarmed = await self._alarm_pathological()
        await self._emit_alarm_audit(alarmed)
        # Phase C: embedding backfill — guarded by its own interval so
        # the Curator's main tick can run faster without hammering the
        # embedding API.
        await self._maybe_emit_embeddings()

    # ------------------------------------------------------------------
    # Decay — Phase B spec §4 + archived plan Task 5.2.
    # ------------------------------------------------------------------

    async def _apply_natural_decay(self) -> None:
        """Single UPDATE applying exponential decay to all eligible slices.

        Formula: ``salience *= POWER(0.5, dt / half_life)``
        where ``dt = now() - salience_updated_at`` and
        ``half_life = dp.natural_half_life``.

        Skips:
        * Slices with ``salience_updated_at`` within ``_DECAY_MIN_INTERVAL_SECONDS``
          (decay against tiny dt is mathematically noise, not signal).
        * ``sentinel_state != 'passed'`` (pending + quarantined are Sentinel territory).
        * ``consolidation_state = 'released'`` (already at 0; no-op).
        """
        import hermes_db

        async with hermes_db.transaction() as conn:
            await conn.execute(
                """
                UPDATE substrate_slices sl
                   SET salience_score = sl.salience_score *
                       POWER(
                           0.5,
                           EXTRACT(EPOCH FROM (now() - sl.salience_updated_at))
                           / GREATEST(EXTRACT(EPOCH FROM dp.natural_half_life), 0.001)
                       ),
                       salience_updated_at = now()
                  FROM substrate_streams st
                  JOIN substrate_decay_profiles dp ON dp.profile_id = st.decay_profile_id
                 WHERE sl.stream_id           = st.stream_id
                   AND sl.sentinel_state      = 'passed'
                   AND sl.consolidation_state <> 'released'
                   AND now() - sl.salience_updated_at > interval '1 second'
                """
            )

    # ------------------------------------------------------------------
    # Release — Phase B spec §6 + archived plan Tasks 5.4–5.5.
    # ------------------------------------------------------------------

    async def _evaluate_releases(self) -> list["ReleaseRecord"]:
        """Read + release up to ``RELEASE_BATCH_LIMIT`` eligible slices.

        Eligibility is the SliceRepo.release_eligible CTE: passed,
        not-released, below the per-profile salience floor, and either
        the profile does not require consolidation before release OR the
        slice is consolidated.
        """
        import hermes_db

        async with hermes_db.transaction() as conn:
            return await self._substrate.slices.release_eligible(
                conn, limit=self.RELEASE_BATCH_LIMIT
            )

    async def _emit_release_audit(self, released: list["ReleaseRecord"]) -> None:
        """Emit one ``curator.release`` slice on ``substrate.self_state``
        per released slice. Bounded by the LIMIT in evaluate_releases.

        Lookup-then-loop pattern matches Phase A's Sentinel batch
        summary. The audit commits in its own connection — the release
        UPDATE has already committed before we get here.
        """
        if not released:
            return

        from substrate.l0.api import commit_slice

        self_state = await self._substrate.streams.get_by_name(
            "substrate.self_state"
        )
        if self_state is None:
            self._log.warning(
                "substrate.self_state stream missing; can't emit release audit"
            )
            return

        now = datetime.now(timezone.utc)
        for r in released:
            await commit_slice(
                self._substrate,
                stream_id=self_state.stream_id,
                payload={
                    "event": "curator.release",
                    "slice_id": str(r.slice_id),
                    "stream_id": str(r.stream_id),
                    "tombstone_policy": r.tombstone_policy,
                    "salience_at_release": float(r.salience_at_release),
                    "released_at": now.isoformat(),
                },
                event_time_world=now,
                metadata={"agent": "curator"},
            )

    # ------------------------------------------------------------------
    # Pathological-forgetting alarm — Phase B spec §7 + archived plan
    # Task 5.7. Slices past their profile's consolidation_window still
    # unconsolidated get bumped + emit an alarm self-state slice.
    # ------------------------------------------------------------------

    async def _alarm_pathological(self) -> list[dict]:
        """Find + bump + report up to ``ALARM_BATCH_LIMIT`` overdue slices.

        Returns the per-alarm dicts so ``_emit_alarm_audit`` can write
        them without re-querying.
        """
        import hermes_db

        # Read + bump in one transaction so concurrent Curators don't
        # double-bump. The SELECT uses FOR UPDATE SKIP LOCKED.
        alarmed: list[dict] = []
        async with hermes_db.transaction() as conn:
            rows = await conn.fetch(
                """
                SELECT sl.slice_id, sl.stream_id, sl.ingest_time_world,
                       EXTRACT(EPOCH FROM (now() - sl.ingest_time_world))::bigint AS age_seconds,
                       EXTRACT(EPOCH FROM dp.consolidation_window)::bigint AS window_seconds,
                       dp.reinforcement_bump
                  FROM substrate_slices         sl
                  JOIN substrate_streams        st ON st.stream_id  = sl.stream_id
                  JOIN substrate_decay_profiles dp ON dp.profile_id = st.decay_profile_id
                 WHERE sl.sentinel_state      = 'passed'
                   AND sl.consolidation_state = 'unconsolidated'
                   AND sl.ingest_time_world + dp.consolidation_window < now()
                 ORDER BY sl.ingest_time_world ASC
                 LIMIT $1
                 FOR UPDATE OF sl SKIP LOCKED
                """,
                self.ALARM_BATCH_LIMIT,
            )
            for r in rows:
                bump = float(r["reinforcement_bump"])
                # Bump in the same txn so the SELECT lock chain stays
                # tight. Returns the post-bump salience for the audit.
                post = await conn.fetchval(
                    """
                    UPDATE substrate_slices
                       SET salience_score = LEAST(1.0, salience_score + $2),
                           salience_updated_at = now()
                     WHERE slice_id = $1 AND ingest_time_world = $3
                    RETURNING salience_score
                    """,
                    r["slice_id"],
                    bump,
                    r["ingest_time_world"],
                )
                alarmed.append(
                    {
                        "slice_id": r["slice_id"],
                        "stream_id": r["stream_id"],
                        "age_seconds": int(r["age_seconds"]),
                        "window_seconds": int(r["window_seconds"]),
                        "bumped_to": float(post) if post is not None else None,
                    }
                )
        return alarmed

    # ------------------------------------------------------------------
    # Phase C: embedding emit (spec §5.7).
    # ------------------------------------------------------------------

    async def _maybe_emit_embeddings(self) -> None:
        """Run the embedding-backfill batch if enough wall-clock time
        has passed since the last cycle. ``RECALL_EMBEDDING_BACKFILL_INTERVAL_S``
        is the gate; the Curator's main tick cadence may be faster."""
        from substrate import config as _cfg

        now = time.monotonic()
        if (now - self._last_embed_backfill_at) < _cfg.RECALL_EMBEDDING_BACKFILL_INTERVAL_S:
            return
        self._last_embed_backfill_at = now
        await self._emit_embeddings_for_unembedded()

    async def _emit_embeddings_for_unembedded(self) -> None:
        """One backfill pass: read up to ``RECALL_EMBEDDING_BACKFILL_BATCH_SIZE``
        unembedded passed slices, batch-call the embedding client,
        persist each result via ``SliceRepo.set_embedding``.

        Per-slice failures (None vector returned, or set_embedding
        raised) increment the in-process retry counter; once a slice
        hits ``RECALL_EMBEDDING_BACKFILL_MAX_RETRIES`` consecutive
        failures it's marked ``embedding_failed=true`` in metadata and
        the next ``list_unembedded`` excludes it.
        """
        from substrate import config as _cfg

        import hermes_db

        async with hermes_db.connection() as conn:
            rows = await self._substrate.slices.list_unembedded(
                conn, limit=_cfg.RECALL_EMBEDDING_BATCH_SIZE
            )
        if not rows:
            return

        texts = [_extract_text_for_embedding(r["payload"]) for r in rows]
        try:
            vectors = await embed(
                texts,
                model=_cfg.RECALL_EMBEDDING_MODEL,
                timeout_ms=_cfg.RECALL_EMBEDDING_TIMEOUT_MS,
            )
        except Exception as exc:
            self._log.warning("curator embed batch raised: %s", exc)
            # Whole-batch failure: bump each slice's retry counter.
            for r in rows:
                self._record_embed_failure(r["slice_id"])
            await self._persist_failures_if_maxed(rows)
            return

        async with hermes_db.connection() as conn:
            async with conn.transaction():
                for row, vec in zip(rows, vectors):
                    if vec is None:
                        self._record_embed_failure(row["slice_id"])
                        continue
                    try:
                        await self._substrate.slices.set_embedding(
                            conn, row["slice_id"], vec
                        )
                        self._reset_embed_failure(row["slice_id"])
                    except Exception as exc:
                        self._log.warning(
                            "curator set_embedding for %s failed: %s",
                            row["slice_id"],
                            exc,
                        )
                        self._record_embed_failure(row["slice_id"])
            # Persist failures outside the embedding transaction so a
            # bad slice can't block the rest of the batch from landing.
            await self._persist_failures_if_maxed(rows)

    def _record_embed_failure(self, slice_id: UUID) -> None:
        self._embed_failure_counts[slice_id] = (
            self._embed_failure_counts.get(slice_id, 0) + 1
        )

    def _reset_embed_failure(self, slice_id: UUID) -> None:
        self._embed_failure_counts.pop(slice_id, None)

    async def _persist_failures_if_maxed(self, rows: list[dict]) -> None:
        """For each slice whose failure count has reached the cap,
        persist metadata.embedding_failed=true and drop the in-process
        counter."""
        from substrate import config as _cfg

        import hermes_db

        to_persist: list[UUID] = []
        cap = _cfg.RECALL_EMBEDDING_BACKFILL_MAX_RETRIES
        for r in rows:
            sid = r["slice_id"]
            count = self._embed_failure_counts.get(sid, 0)
            if count >= cap:
                to_persist.append(sid)
        if not to_persist:
            return
        async with hermes_db.transaction() as conn:
            for sid in to_persist:
                try:
                    await self._substrate.slices.mark_embedding_failed(conn, sid)
                except Exception as exc:
                    self._log.warning(
                        "mark_embedding_failed for %s raised: %s", sid, exc
                    )
                # Drop the counter regardless — the DB marker is now
                # authoritative for whether this slice gets retried.
                self._embed_failure_counts.pop(sid, None)

    async def _emit_alarm_audit(self, alarmed: list[dict]) -> None:
        """Emit one ``curator.pathological_forgetting_alarm`` slice on
        ``substrate.self_state`` per alarmed slice."""
        if not alarmed:
            return

        from substrate.l0.api import commit_slice

        self_state = await self._substrate.streams.get_by_name(
            "substrate.self_state"
        )
        if self_state is None:
            self._log.warning(
                "substrate.self_state stream missing; can't emit alarm audit"
            )
            return

        now = datetime.now(timezone.utc)
        for a in alarmed:
            await commit_slice(
                self._substrate,
                stream_id=self_state.stream_id,
                payload={
                    "event": "curator.pathological_forgetting_alarm",
                    "slice_id": str(a["slice_id"]),
                    "stream_id": str(a["stream_id"]),
                    "age_seconds": a["age_seconds"],
                    "consolidation_window_seconds": a["window_seconds"],
                    "bumped_to": a["bumped_to"],
                    "alarmed_at": now.isoformat(),
                },
                event_time_world=now,
                metadata={"agent": "curator"},
            )


def _extract_text_for_embedding(payload) -> str:
    """Best-effort text extraction for the embedding API.

    * ``str`` (already-unwrapped text-modality payload) passes through.
    * ``{"text": "..."}`` (text-modality JSONB envelope from the L0
      writer) unwraps to bare string.
    * Other dicts (structured events) are JSON-serialised with
      deterministic key ordering so retries on the same payload
      embed identical text.
    * Anything else is str()'d as a fallback.

    Empty / whitespace-only output is allowed — the embedding API
    handles short strings; the result will be a degenerate but unit
    vector. The recall pipeline degrades gracefully (cosine of a
    degenerate vector against any query is just whatever the model
    produces; the ranker handles it).
    """
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        text_field = payload.get("text")
        if isinstance(text_field, str):
            return text_field
        import json

        try:
            return json.dumps(payload, sort_keys=True, separators=(",", ":"))
        except Exception:
            return str(payload)
    return str(payload)


__all__ = ["Curator"]
