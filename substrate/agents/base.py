"""``SubAgent`` base class + intensity dial.

Every substrate sub-agent (Sentinel, Curator, Reflector, ...) subclasses
``SubAgent`` so the lifecycle and intensity-dial machinery are uniform.
Phase A ships stubs (Sentinel passes everything, Conductor holds state
with no policy) but the contract here is the **same** one Phase B+ real
sub-agents will honor.

Intensity is per-agent: each agent reads a :class:`Level` and sleeps
between ticks proportional to it (see ``_INTERVAL_BY_LEVEL`` below).
``OFF`` is a hard stop — ``tick()`` is never called. The mapping is
deliberately conservative; Phase B+ may add per-agent overrides via a
class attribute.

Sentinel agents (``is_sentinel = True``) have a floor of ``FULL`` —
``set_intensity()`` silently coerces below-FULL settings back to FULL.
This enforces "Sentinel is never throttled" as a design invariant.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from substrate.facade import Substrate


# ---------------------------------------------------------------------------
# Intensity dial — 5-level. Mirrors the named-mode collapse from the MVS
# spec §3.5 (Ambient/Burst/Reactive/Rising/Background → LOW/MODERATE/HIGH/
# FULL/OFF). Levels are strings on the wire so Conductor and operator-side
# tooling can read them without enum imports.
# ---------------------------------------------------------------------------


class Level(str, Enum):
    """Sub-agent intensity. ``OFF`` is a hard stop (no tick); ``FULL``
    is "run as often as the implementation can".
    """

    OFF = "off"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    FULL = "full"


# Tick interval (seconds) per level. Returning ``None`` for OFF lets
# the run loop skip ``tick()`` without busy-waiting.
_INTERVAL_BY_LEVEL: dict[Level, Optional[float]] = {
    Level.OFF: None,
    Level.LOW: 10.0,
    Level.MODERATE: 3.0,
    Level.HIGH: 1.0,
    Level.FULL: 0.2,
}


# Sleep used by the run loop when intensity is OFF, so a future
# ``set_intensity(level=FULL)`` can pick up the change within a second.
_OFF_POLL_INTERVAL = 1.0


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class SubAgent(ABC):
    """Common scaffold for every substrate sub-agent.

    Subclasses must:
      * Set ``name`` (class attribute) — used for logging and the
        inspect CLI.
      * Set ``is_sentinel`` if the agent is a defensive primitive that
        must never be throttled (Sentinel itself, and any future
        always-on auditor).
      * Implement ``async tick()`` — one unit of work; called inside
        the run loop with no arguments. Exceptions are caught and
        logged; the loop continues.
    """

    name: str = "unnamed"
    is_sentinel: bool = False

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate
        # Sentinels start at FULL (which is also their floor). Other
        # agents start at LOW — they wake up but don't burn cycles
        # until Conductor (Phase B+) tells them otherwise.
        self._level: Level = Level.FULL if self.is_sentinel else Level.LOW
        self._stopped: asyncio.Event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._log = logging.getLogger(f"substrate.agents.{self.name}")

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    def set_intensity(self, level: Level) -> None:
        """Change the sub-agent's intensity.

        Sentinels are silently floored at ``FULL``. Calling
        ``set_intensity(OFF)`` on a Sentinel is a no-op; the choice is
        intentional — the floor is a design invariant, and a noisy
        error would force the caller to special-case Sentinel in
        Conductor's bulk-dial code.
        """
        if self.is_sentinel and level is not Level.FULL:
            self._log.debug(
                "ignoring set_intensity(%s) — sentinel floor is FULL",
                level.value,
            )
            level = Level.FULL
        self._level = level

    @property
    def level(self) -> Level:
        """Current intensity level — useful for the inspect CLI."""
        return self._level

    async def run(self) -> None:
        """Main loop. Sleeps per current intensity, calls ``tick()``,
        respects the stopped event.

        Exceptions in ``tick()`` are logged and the loop continues —
        Phase A sub-agents must never crash the substrate process.
        Phase B+ may introduce circuit-breaker behavior; not yet.

        Sleeps via ``wait_for(self._stopped.wait(), timeout=...)`` so
        ``stop()`` wakes the loop immediately instead of waiting out
        the full tick interval. With a plain ``asyncio.sleep(interval)``
        the partition-maintenance agent (24h cadence) and force-reject
        (3–10s) would always exceed the 2-second shutdown grace and
        log spurious ``subagent.stop.timeout`` warnings on clean exit.
        """
        self._log.debug(
            "subagent.run.start name=%s level=%s", self.name, self._level.value
        )

        async def _wait(seconds: float) -> None:
            """Sleep for *seconds*, but return early if ``stop()`` is called."""
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=seconds)
            except asyncio.TimeoutError:
                pass  # normal: the interval elapsed without a stop request

        try:
            while not self._stopped.is_set():
                # Call ``self._interval_for(...)`` so subclasses can
                # override the mapping (e.g. partition-maintenance
                # forces a fixed 24h cadence regardless of intensity).
                # The base implementation looks up _INTERVAL_BY_LEVEL.
                interval = self._interval_for(self._level)
                if interval is None:  # OFF
                    await _wait(_OFF_POLL_INTERVAL)
                    continue
                try:
                    await self.tick()
                except Exception:
                    # Log with exc_info so the traceback lands in the
                    # substrate's log; never re-raise to the loop.
                    self._log.exception("subagent.tick.error name=%s", self.name)
                await _wait(interval)
        finally:
            self._log.debug("subagent.run.stop name=%s", self.name)

    def stop(self) -> None:
        """Request graceful stop. The run loop checks ``_stopped`` at
        the top of each iteration; in flight ``tick()`` completes."""
        self._stopped.set()

    async def stop_and_wait(self, *, timeout: float = 2.0) -> None:
        """Stop and wait for the run loop's task to exit.

        Used by :meth:`Substrate.shutdown`. The timeout is a safety
        net — a misbehaving ``tick()`` shouldn't hang shutdown
        indefinitely.
        """
        self.stop()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._task, timeout=timeout)
        except asyncio.TimeoutError:
            self._log.warning(
                "subagent.stop.timeout name=%s timeout=%s", self.name, timeout
            )
            self._task.cancel()

    def start(self) -> asyncio.Task:
        """Spawn the run loop as an asyncio task and return the task
        handle. Idempotent: calling ``start()`` twice returns the
        existing task.
        """
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name=f"substrate-{self.name}")
        return self._task

    @property
    def task(self) -> Optional[asyncio.Task]:
        """The asyncio task created by :meth:`start`, or ``None`` if
        the agent hasn't been started yet."""
        return self._task

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------

    @abstractmethod
    async def tick(self) -> None:
        """One unit of work. Called from the run loop. Must not block
        on user-facing operations. Exceptions are caught + logged by
        the run loop; subclasses must NOT swallow them silently."""

    # ------------------------------------------------------------------
    # Testing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _interval_for(level: Level) -> Optional[float]:
        """Public test seam — return the configured tick interval for
        ``level``. Returns ``None`` for ``OFF``.
        """
        return _INTERVAL_BY_LEVEL[level]


__all__ = ["Level", "SubAgent"]
