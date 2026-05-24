"""Stub Conductor — Phase A intensity-vector holder.

Phase A's Conductor is a pure data holder: it stores per-sub-agent
intensity levels and exposes get/set accessors. **No tick loop** —
nothing reads the conductor's outputs yet (no real sub-agents to
dial). The interface here is what the real Phase B+ Conductor (with
forecasting + mode-vector dispatch + scheduling) will honor when it
arrives.

Note: this class deliberately does NOT subclass :class:`SubAgent` —
the conductor isn't a sub-agent itself; it commands them. Real
Conductor (Phase B+) may run its own forecasting loop, at which point
it'll subclass SubAgent + override ``tick()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from substrate.agents.base import Level

if TYPE_CHECKING:  # pragma: no cover
    from substrate.facade import Substrate


class StubConductor:
    """Holds per-agent intensity levels.

    Initial defaults:
      * Sentinels start at ``FULL`` (they're floored there anyway).
      * Everything else starts at ``LOW``.

    A real Conductor will set per-agent levels in response to load,
    mode (focus / explore / dream), and Conductor-internal forecasts.
    The stub just preserves whatever the operator (or test) sets.
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate
        # name → Level. Populated lazily on first get / set so we
        # don't need to know the list of sub-agents at __init__.
        self._levels: dict[str, Level] = {}

    def set_intensity(self, agent_name: str, level: Level) -> None:
        """Override the stored intensity for ``agent_name``.

        Note: setting the level here does NOT update a running agent's
        ``_level`` — Phase B+ wires the Conductor → SubAgent push.
        For now, sub-agents fetch on tick or at boot.
        """
        self._levels[agent_name] = level

    def intensity_for(self, agent_name: str, *, is_sentinel: bool = False) -> Level:
        """Return the level for an agent, defaulting based on whether
        it's a sentinel."""
        if agent_name in self._levels:
            return self._levels[agent_name]
        # Sensible defaults: sentinels at FULL (floor); others at LOW.
        return Level.FULL if is_sentinel else Level.LOW

    def snapshot(self) -> dict[str, Level]:
        """Return a shallow copy of the level mapping — used by the
        inspect CLI."""
        return dict(self._levels)


__all__ = ["StubConductor"]
