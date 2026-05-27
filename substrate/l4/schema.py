"""L4 dataclass — a self-model / calibration observation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

OBSERVATION_KINDS = frozenset({"coherence", "calibration", "bias", "other"})


@dataclass(frozen=True)
class Observation:
    id: UUID
    kind: str
    subject: str
    statement: str
    score: Optional[float]
    created_at: datetime
    metadata: dict[str, Any]


__all__ = ["OBSERVATION_KINDS", "Observation"]
