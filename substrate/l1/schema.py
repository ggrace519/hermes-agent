"""L1 typed dataclasses — entities, relationships, citations, and the
Parser's extraction output shape.

Two families of types live here:

* **Stored rows** — :class:`Entity`, :class:`Relationship`, :class:`Citation`
  mirror the ``l1_entities`` / ``l1_relationships`` / ``l1_citations`` tables
  (migration ``20260527_0011``). Returned by the read helpers in
  :mod:`substrate.l1.store`.
* **Extraction output** — :class:`ParsedEntity`, :class:`ParsedRelationship`,
  :class:`ParserResult` are what the Parser's LLM step (Phase D2's
  ``substrate.l1.extract``) produces and what
  :func:`substrate.l1.store.persist_extraction` consumes. Keeping them here
  (not in ``extract.py``) lets the store + its tests build extraction
  results without importing the LLM machinery — the whole L1 write path is
  exercisable offline.

Per Phase D spec (2026-05-25-phase-d-l1-parser.md) §2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from uuid import UUID


# Allowed entity_type values — mirrors the CHECK constraint in
# migration 20260527_0011. The Parser's extraction is normalised to this
# set (anything off-list collapses to "other") before it reaches the store.
ENTITY_TYPES: frozenset[str] = frozenset(
    {"person", "project", "file", "concept", "place", "org", "other"}
)


def normalise_entity_type(raw: Optional[str]) -> str:
    """Map an arbitrary LLM-supplied type to an allowed value.

    Defensive: a model that returns ``"Person"`` or an unknown kind must
    not trip the DB CHECK constraint. Unknowns become ``"other"``.
    """
    if not raw:
        return "other"
    t = raw.strip().lower()
    return t if t in ENTITY_TYPES else "other"


# ---------------------------------------------------------------------------
# Stored rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Entity:
    id: UUID
    name: str
    entity_type: str
    summary: str
    aliases: list[str]
    salience_score: float
    created_at: datetime
    last_seen_at: datetime
    extra: dict[str, Any]


@dataclass(frozen=True)
class Relationship:
    id: UUID
    subject_id: UUID
    predicate: str
    object_id: UUID
    confidence: float
    created_at: datetime
    last_seen_at: datetime
    extra: dict[str, Any]


@dataclass(frozen=True)
class Citation:
    id: UUID
    entity_id: Optional[UUID]
    relationship_id: Optional[UUID]
    slice_id: UUID
    quote: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Extraction output (Parser → store)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedEntity:
    name: str
    entity_type: str
    summary: str = ""
    aliases: list[str] = field(default_factory=list)
    source_slice_ids: list[UUID] = field(default_factory=list)
    quote: str = ""


@dataclass(frozen=True)
class ParsedRelationship:
    subject_name: str
    subject_type: str
    predicate: str
    object_name: str
    object_type: str
    confidence: float = 0.7
    source_slice_ids: list[UUID] = field(default_factory=list)
    quote: str = ""


@dataclass(frozen=True)
class ParserResult:
    entities: list[ParsedEntity] = field(default_factory=list)
    relationships: list[ParsedRelationship] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.entities and not self.relationships


__all__ = [
    "ENTITY_TYPES",
    "normalise_entity_type",
    "Entity",
    "Relationship",
    "Citation",
    "ParsedEntity",
    "ParsedRelationship",
    "ParserResult",
]
