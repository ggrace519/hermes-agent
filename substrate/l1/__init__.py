"""L1 — entities, relationships, and their citations to L0 slices.

The first structured layer above L0 (raw perception). The Parser sub-agent
(Phase D) distils ``passed`` L0 slices into entities + relationships, each
citing the slice it came from, and runs the consolidation handshake
(design §5.7) that lets the Curator release the raw slice while its meaning
lives on here.

Public surface re-exported for convenience; storage helpers live in
:mod:`substrate.l1.store`, types in :mod:`substrate.l1.schema`.
"""

from __future__ import annotations

from substrate.l1.schema import (
    Citation,
    Entity,
    ParsedEntity,
    ParsedRelationship,
    ParserResult,
    Relationship,
)

__all__ = [
    "Entity",
    "Relationship",
    "Citation",
    "ParsedEntity",
    "ParsedRelationship",
    "ParserResult",
]
