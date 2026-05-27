"""Phase F — adaptive Conductor policy (the executive's first real loop).

The Conductor is the substrate's executive function (MVS §3.6): it reads
operational state and dials sub-agent intensities. Phase A/B shipped the
``StubConductor`` — an intensity registry that holds + pushes levels but
has no policy. This adds a **deterministic adaptive policy** that ticks,
reads observable load, and drives the StubConductor:

* **Consolidation backlog high** → raise the Parser (catch up), and pause
  the enrichment agents (Associator / Pattern-finder → OFF) so scarce
  cycles go to parsing first.
* **Backlog low** → everyone back to baseline LOW.

It mutates intensities only through ``substrate.conductor.set_intensity``
(which pushes to running agents + enforces each agent's floor — Sentinel
stays FULL, Curator stays ≥ LOW). Gated by ``HERMES_SUBSTRATE_CONDUCTOR``
(default OFF → the static Phase A/B behaviour is preserved exactly).

This is the *deterministic* Conductor. The **learned** Conductor
(opportunity forecasting, intensity-policy learning, worklist scheduling,
wake anticipation — MVS §3.6) remains deferred research, flagged for review.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from substrate.agents.base import Level, SubAgent

if TYPE_CHECKING:  # pragma: no cover
    from substrate.facade import Substrate


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"} if raw else default


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


class AdaptiveConductor(SubAgent):
    """Deterministic intensity policy. Floor LOW; gated OFF by default."""

    name = "conductor"
    is_sentinel = False

    def __init__(self, substrate: "Substrate") -> None:
        super().__init__(substrate)
        self._level = Level.LOW

    async def tick(self) -> None:
        if not _env_bool("HERMES_SUBSTRATE_CONDUCTOR", default=False):
            return
        if self._level is Level.OFF:
            return

        signals = await self._read_load()
        targets = self._compute_targets(signals)
        conductor = getattr(self._substrate, "conductor", None)
        if conductor is None:
            return
        for agent_name, level in targets.items():
            conductor.set_intensity(agent_name, level)
        await self._emit_self_state(signals, targets)

    async def _read_load(self) -> dict:
        import hermes_db

        async with hermes_db.connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) FILTER (
                          WHERE consolidation_state='unconsolidated'
                            AND sentinel_state='passed')::int AS pending,
                       COUNT(*) FILTER (WHERE consolidation_state='consolidated')::int AS done
                  FROM substrate_slices
                """
            )
        denom = row["pending"] + row["done"]
        return {
            "pending": row["pending"],
            "consolidated": row["done"],
            "backlog_ratio": (row["pending"] / denom) if denom else 0.0,
        }

    @staticmethod
    def _compute_targets(signals: dict) -> dict[str, Level]:
        """Deterministic policy → target intensity per agent. Pure (testable)."""
        high = _env_float("CONDUCTOR_BACKLOG_HIGH", 0.5)
        low = _env_float("CONDUCTOR_BACKLOG_LOW", 0.1)
        backlog = signals["backlog_ratio"]

        if backlog >= high:
            # Catch up: parser hard, enrichment paused.
            return {
                "parser": Level.HIGH,
                "associator": Level.OFF,
                "pattern-finder": Level.OFF,
                "curator": Level.LOW,
            }
        if backlog >= low:
            return {
                "parser": Level.MODERATE,
                "associator": Level.LOW,
                "pattern-finder": Level.LOW,
                "curator": Level.LOW,
            }
        # Quiet: baseline.
        return {
            "parser": Level.LOW,
            "associator": Level.LOW,
            "pattern-finder": Level.LOW,
            "curator": Level.LOW,
        }

    async def _emit_self_state(self, signals: dict, targets: dict) -> None:
        from substrate.l0.api import commit_slice

        self_state = await self._substrate.streams.get_by_name("substrate.self_state")
        if self_state is None:
            return
        now = datetime.now(timezone.utc)
        try:
            await commit_slice(
                self._substrate,
                stream_id=self_state.stream_id,
                payload={
                    "event": "conductor.dialed",
                    "backlog_ratio": signals["backlog_ratio"],
                    "targets": {k: v.value for k, v in targets.items()},
                    "at": now.isoformat(),
                },
                event_time_world=now,
                metadata={"agent": "conductor"},
            )
        except Exception:
            self._log.debug("conductor.self_state.emit_failed", exc_info=True)


__all__ = ["AdaptiveConductor"]
