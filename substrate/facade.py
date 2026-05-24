"""``class Substrate`` — the public lifecycle + accessor surface.

Phase A scope: just enough to back :func:`substrate.l0.api.commit_slice`
and the test fixtures. The full boot story (Alembic-head check,
stream auto-registration, sub-agent task spawn, hook binding) lands in
Task 14 of the Phase A plan.

This module is intentionally minimal so the L0 API has a stable
``substrate.streams`` / ``substrate.slices`` / ``substrate.pool``
interface to depend on. Subsequent tasks layer on top.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    import asyncpg
    import logging as _logging

from substrate.config import SubstrateConfig
from substrate.storage import DecayProfileRepo, SliceRepo, StreamRepo


class Substrate:
    """Process-wide substrate handle.

    Constructed via :meth:`boot` (recommended) so that future setup
    (sub-agent task spawn, stream auto-registration) is centralised.
    Direct construction is allowed in tests via the
    :meth:`from_pool` classmethod.
    """

    def __init__(
        self,
        *,
        pool: "asyncpg.Pool",
        streams: StreamRepo,
        slices: SliceRepo,
        profiles: DecayProfileRepo,
        log: "_logging.Logger",
        config: SubstrateConfig,
    ) -> None:
        self.pool = pool
        self.streams = streams
        self.slices = slices
        self.profiles = profiles
        self.log = log
        self.config = config

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_pool(
        cls,
        pool: "asyncpg.Pool",
        *,
        log: "Optional[_logging.Logger]" = None,
        config: Optional[SubstrateConfig] = None,
    ) -> "Substrate":
        """Build a Substrate without running boot-time side effects.

        Intended for tests and for callers that want to compose the
        full boot themselves. The proper public entry point is
        :meth:`boot`, which lands in Phase A Task 14 and wraps this
        with Alembic-head check + stream auto-registration +
        sub-agent task spawn.
        """
        import logging

        return cls(
            pool=pool,
            streams=StreamRepo(pool),
            slices=SliceRepo(pool),
            profiles=DecayProfileRepo(pool),
            log=log or logging.getLogger("substrate"),
            config=config or SubstrateConfig.from_env(),
        )

    @classmethod
    async def boot(
        cls,
        *,
        log: "Optional[_logging.Logger]" = None,
        config: Optional[SubstrateConfig] = None,
        start_subagents: bool = True,
    ) -> "Substrate":
        """Full boot — Phase A Task 14 will fill this in.

        For now we provide a thin scaffold that:
          1. Asserts ``hermes_db.pool()`` is initialised (raises if
             not).
          2. Constructs the substrate via :meth:`from_pool`.

        Task 14 adds: Alembic-head check, stream auto-registration
        (the 15 streams from spec §9), sub-agent task spawn
        (Sentinel + force-reject + partition-maintenance), hook
        binding.
        """
        import hermes_db

        pool = hermes_db.pool()  # raises if init() not called
        substrate = cls.from_pool(pool, log=log, config=config)
        # Task 14: add Alembic-head check + stream auto-registration
        # + sub-agent task spawn + hook binding here.
        return substrate

    async def shutdown(self) -> None:
        """Stop sub-agents, flush pending audit emissions.

        Task 14 will fill this in. The asyncpg pool is NOT closed here
        — that belongs to ``hermes_db.close()`` which is owned by
        Hermes's own shutdown sequence.
        """
        # Task 14: cancel sub-agent tasks; await with timeout.
        return


__all__ = ["Substrate"]
