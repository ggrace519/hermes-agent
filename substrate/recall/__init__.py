"""Substrate recall — read-side API + ranking + composition.

Phase C subpackage. See the
[Phase C spec](https://github.com/ggrace519/llm-cognitive-thought/blob/main/docs/superpowers/specs/2026-05-25-phase-c-recall.md)
for the full design.

Public surface:

    from substrate.recall import (
        recall,
        recall_sync,
        RecallProjection,
        RecallCandidate,
    )

The pipeline is:
    embed_query (optional) → recall_window (SQL) → rank_candidates
        → compose_projection (token-budgeted) → reinforce_hits → log

Failures are absorbed — ``recall()`` always returns a
:class:`RecallProjection` (possibly empty with ``empty_reason`` set).
The :class:`SubstrateMemoryProvider` is the production caller; tests
call ``recall()`` / ``recall_sync()`` directly.
"""

from substrate.recall.projection import RecallCandidate, RecallProjection

__all__ = [
    "RecallCandidate",
    "RecallProjection",
]


def __getattr__(name):
    # Lazy import for the runtime API surface so this package can be
    # imported (e.g. by the dataclasses for type hints) without pulling
    # in the full pipeline machinery.
    if name in {"recall", "recall_sync"}:
        from substrate.recall.api import recall, recall_sync

        if name == "recall":
            return recall
        return recall_sync
    raise AttributeError(f"module 'substrate.recall' has no attribute {name!r}")
