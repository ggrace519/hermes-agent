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

See [Phase B spec](https://github.com/ggrace519/llm-cognitive-thought/blob/main/docs/superpowers/specs/2026-05-25-phase-b-curator.md)
§4 (Curator's loop), §6 (release), §7 (self-state emission).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from substrate.agents.base import Level, SubAgent

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


__all__ = ["Curator"]
