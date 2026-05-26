"""Substrate configuration — read once at boot.

Phase A introduced the minimal SubstrateConfig dataclass. Phase C adds
the recall + embedding knobs (spec §5.6) as module-level constants
read at import time from ``HERMES_RECALL_*`` env vars (with sane
defaults). Module-level rather than a dataclass because the recall
pipeline + Curator embedding loop read these in hot paths and a
per-call dataclass lookup is unnecessary overhead.

Mutating these constants at runtime is unsupported — set the env vars
before importing the recall package.
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


def _envint(name: str, *, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _envfloat(name: str, *, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Phase C — recall knobs (spec §5.6).
# ---------------------------------------------------------------------------

# Pipeline budgets.
RECALL_TOKEN_BUDGET = _envint("HERMES_RECALL_TOKEN_BUDGET", default=1500)
RECALL_TIME_WINDOW_HOURS = _envfloat("HERMES_RECALL_TIME_WINDOW_HOURS", default=24.0)
RECALL_TIMEOUT_MS = _envint("HERMES_RECALL_TIMEOUT_MS", default=300)
RECALL_MIN_SALIENCE = _envfloat("HERMES_RECALL_MIN_SALIENCE", default=0.05)
RECALL_CANDIDATE_LIMIT = _envint("HERMES_RECALL_CANDIDATE_LIMIT", default=50)

# Composite-score weights (must keep sum of three active terms in a
# reasonable range; spec defaults sum to 1.0 for the active path).
RECALL_SIMILARITY_WEIGHT = _envfloat("HERMES_RECALL_SIMILARITY_WEIGHT", default=0.3)
RECALL_KEYWORD_WEIGHT = _envfloat("HERMES_RECALL_KEYWORD_WEIGHT", default=0.3)
RECALL_SALIENCE_WEIGHT = _envfloat("HERMES_RECALL_SALIENCE_WEIGHT", default=0.5)
RECALL_RECENCY_WEIGHT = _envfloat("HERMES_RECALL_RECENCY_WEIGHT", default=0.2)
RECALL_RECENCY_HALF_LIFE_HOURS = _envfloat(
    "HERMES_RECALL_RECENCY_HALF_LIFE_HOURS", default=12.0
)

# Anti-thrashing: per-slice reinforcement cap per minute (spec §5.4).
RECALL_REINFORCE_RATE_LIMIT_PER_MIN = _envint(
    "HERMES_RECALL_REINFORCE_RATE_LIMIT_PER_MIN", default=6
)

# Recall log writer.
RECALL_LOG_QUEUE_DEPTH = _envint("HERMES_RECALL_LOG_QUEUE_DEPTH", default=1024)

# Embedding pipeline.
RECALL_EMBEDDING_MODEL = os.environ.get(
    "HERMES_RECALL_EMBEDDING_MODEL", "text-embedding-3-small"
)
RECALL_EMBEDDING_DIM = _envint("HERMES_RECALL_EMBEDDING_DIM", default=1536)
RECALL_EMBEDDING_TIMEOUT_MS = _envint(
    "HERMES_RECALL_EMBEDDING_TIMEOUT_MS", default=800
)
RECALL_EMBEDDING_QUEUE_DEPTH = _envint(
    "HERMES_RECALL_EMBEDDING_QUEUE_DEPTH", default=4096
)
RECALL_EMBEDDING_BATCH_SIZE = _envint(
    "HERMES_RECALL_EMBEDDING_BATCH_SIZE", default=32
)
RECALL_EMBEDDING_BACKFILL_INTERVAL_S = _envfloat(
    "HERMES_RECALL_EMBEDDING_BACKFILL_INTERVAL_S", default=30.0
)
RECALL_EMBEDDING_BACKFILL_MAX_RETRIES = _envint(
    "HERMES_RECALL_EMBEDDING_BACKFILL_MAX_RETRIES", default=3
)

# Master toggle for the SubstrateMemoryProvider's prefetch (spec §6.1).
# Default ON: this fork installs the substrate as the primary memory
# backend; recall driving the per-turn <memory-context> is the point.
# Set HERMES_SUBSTRATE_RECALL=0 to fall back to the upstream built-in
# provider exclusively (useful for A/B comparison or debugging).
HERMES_SUBSTRATE_RECALL_ENABLED = _envbool(
    "HERMES_SUBSTRATE_RECALL", default=True
)


# Default stream set for recall (spec §4.3). User-message + assistant-
# response streams; explicitly excludes self-state. ``stream_filter=None``
# in the recall API resolves to this list at call time.
DEFAULT_RECALL_STREAMS: tuple[str, ...] = (
    "hermes.world.user_message.cli",
    "hermes.world.user_message.telegram",
    "hermes.world.user_message.discord",
    "hermes.world.user_message.slack",
    "hermes.world.user_message.whatsapp",
    "hermes.world.user_message.signal",
    "hermes.world.user_message.acp",
    "hermes.self_action.assistant_response",
)


__all__ = [
    "SubstrateConfig",
    "RECALL_TOKEN_BUDGET",
    "RECALL_TIME_WINDOW_HOURS",
    "RECALL_TIMEOUT_MS",
    "RECALL_MIN_SALIENCE",
    "RECALL_CANDIDATE_LIMIT",
    "RECALL_SIMILARITY_WEIGHT",
    "RECALL_KEYWORD_WEIGHT",
    "RECALL_SALIENCE_WEIGHT",
    "RECALL_RECENCY_WEIGHT",
    "RECALL_RECENCY_HALF_LIFE_HOURS",
    "RECALL_REINFORCE_RATE_LIMIT_PER_MIN",
    "RECALL_LOG_QUEUE_DEPTH",
    "RECALL_EMBEDDING_MODEL",
    "RECALL_EMBEDDING_DIM",
    "RECALL_EMBEDDING_TIMEOUT_MS",
    "RECALL_EMBEDDING_QUEUE_DEPTH",
    "RECALL_EMBEDDING_BATCH_SIZE",
    "RECALL_EMBEDDING_BACKFILL_INTERVAL_S",
    "RECALL_EMBEDDING_BACKFILL_MAX_RETRIES",
    "HERMES_SUBSTRATE_RECALL_ENABLED",
    "DEFAULT_RECALL_STREAMS",
]
