"""Substrate configuration — read once at boot.

Phase A keeps configuration minimal. Real config (per-stream intensity
overrides, decay-profile tuning, hook toggles) lands when there are
sub-agents that actually read these values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SubstrateConfig:
    """Frozen at boot — every sub-agent reads from the same snapshot.

    Phase A fields are intentionally narrow. As Phase B+ adds the
    Curator and the LLM sub-agents, this struct grows with their
    per-agent toggles.
    """

    # If true, ``Substrate.boot()`` runs ``alembic upgrade head`` when
    # the database is behind the expected revision. If false (default),
    # boot raises so the operator can decide. Mirrors Hermes's
    # ``HERMES_AUTO_MIGRATE`` convention from the Phase 0 ADR.
    auto_migrate: bool = False

    # Sub-agent boot toggles. Used by tests via
    # ``Substrate.boot(start_subagents=False)`` to take deterministic
    # control of the tick loop; not exposed as env vars in Phase A.
    start_subagents: bool = True

    @classmethod
    def from_env(cls) -> "SubstrateConfig":
        """Read settings from the process environment.

        Booleans are 'truthy if set to 1/true/yes (case-insensitive),
        falsy otherwise' — matches Hermes's convention across
        ``hermes_db`` and ``hermes_bootstrap``.
        """
        return cls(
            auto_migrate=_envbool("HERMES_AUTO_MIGRATE", default=False),
            start_subagents=True,
        )


def _envbool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
