"""L2 dataclasses — associations + their edit history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

# Allowed edge types — mirrors the CHECK in migration 20260527_0013.
EDGE_TYPES = frozenset({"co_occurrence", "shared_neighbor"})


@dataclass(frozen=True)
class Association:
    assoc_id: UUID
    src_id: UUID
    dst_id: UUID
    edge_type: str
    weight: float
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AssociationEdit:
    edit_id: int
    assoc_id: UUID
    at: datetime
    old_weight: Optional[float]
    new_weight: float
    reason: str


__all__ = ["EDGE_TYPES", "Association", "AssociationEdit"]
