"""L3 dataclasses — patterns (stored) + ParsedPattern (Pattern-finder output)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

PATTERN_KINDS = frozenset(
    {"generalization", "theme", "recurring_structure", "other"}
)


def normalise_kind(raw) -> str:
    if not raw:
        return "other"
    k = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    return k if k in PATTERN_KINDS else "other"


@dataclass(frozen=True)
class Pattern:
    id: UUID
    kind: str
    statement: str
    cites: list
    salience_score: float
    confidence: float
    created_at: datetime
    last_seen_at: datetime
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ParsedPattern:
    statement: str
    kind: str = "other"
    entity_names: list[str] = field(default_factory=list)
    confidence: float = 0.5


@dataclass(frozen=True)
class PatternResult:
    patterns: list[ParsedPattern] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.patterns


__all__ = [
    "PATTERN_KINDS",
    "normalise_kind",
    "Pattern",
    "ParsedPattern",
    "PatternResult",
]
