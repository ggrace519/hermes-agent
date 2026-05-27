"""L1 storage helpers — async PG read/write over the ``l1_*`` tables.

Module-level async functions (not a repo class) per the Phase D spec §2,
sharing the Hermes ``hermes_db`` pool. Every function accepts an optional
``conn=`` so a transactional caller (the Parser's consolidation handshake)
can run the whole write — entities, relationships, citations, and the
``substrate_slices`` flip — inside one transaction.

The write path (:func:`persist_extraction` + :func:`mark_slices_consolidated`)
is what makes the consolidation handshake of design §5.7 live; the read
helpers back the recall L1 header (Phase D3) and the inspect CLI.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal, Optional
from uuid import UUID

from substrate.l1.schema import (
    Citation,
    Entity,
    ParserResult,
    Relationship,
    normalise_entity_type,
)


@asynccontextmanager
async def _acquire(conn) -> AsyncIterator[Any]:
    """Yield *conn* if the caller supplied one (transactional reuse), else
    acquire a fresh connection from the shared pool."""
    if conn is not None:
        yield conn
        return
    import hermes_db

    async with hermes_db.connection() as fresh:
        yield fresh


# ---------------------------------------------------------------------------
# Row → dataclass mappers
# ---------------------------------------------------------------------------


def _row_to_entity(r) -> Entity:
    return Entity(
        id=r["id"],
        name=r["name"],
        entity_type=r["entity_type"],
        summary=r["summary"] or "",
        aliases=list(r["aliases"] or []),
        salience_score=float(r["salience_score"]),
        created_at=r["created_at"],
        last_seen_at=r["last_seen_at"],
        extra=r["extra"] or {},
    )


def _row_to_relationship(r) -> Relationship:
    return Relationship(
        id=r["id"],
        subject_id=r["subject_id"],
        predicate=r["predicate"],
        object_id=r["object_id"],
        confidence=float(r["confidence"]),
        created_at=r["created_at"],
        last_seen_at=r["last_seen_at"],
        extra=r["extra"] or {},
    )


def _row_to_citation(r) -> Citation:
    return Citation(
        id=r["id"],
        entity_id=r["entity_id"],
        relationship_id=r["relationship_id"],
        slice_id=r["slice_id"],
        quote=r["quote"] or "",
        created_at=r["created_at"],
    )


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


async def upsert_entity(
    name: str,
    entity_type: str,
    *,
    summary: str = "",
    aliases: Optional[list[str]] = None,
    conn=None,
) -> tuple[UUID, bool]:
    """Insert or merge an entity on ``(name, entity_type)``.

    Returns ``(entity_id, created)``. On conflict: bumps ``last_seen_at``,
    keeps the better summary (a non-empty new one wins), and unions
    aliases. ``created`` uses the ``xmax = 0`` trick (true only on a fresh
    INSERT) so callers can count new vs. re-seen entities.
    """
    etype = normalise_entity_type(entity_type)
    async with _acquire(conn) as c:
        row = await c.fetchrow(
            """
            INSERT INTO l1_entities (name, entity_type, summary, aliases)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (name, entity_type) DO UPDATE SET
                last_seen_at = now(),
                summary = CASE WHEN EXCLUDED.summary <> ''
                               THEN EXCLUDED.summary ELSE l1_entities.summary END,
                aliases = ARRAY(
                    SELECT DISTINCT unnest(l1_entities.aliases || EXCLUDED.aliases)
                )
            RETURNING id, (xmax = 0) AS created
            """,
            name,
            etype,
            summary or "",
            list(aliases or []),
        )
    return row["id"], bool(row["created"])


async def upsert_relationship(
    subject_id: UUID,
    predicate: str,
    object_id: UUID,
    *,
    confidence: float = 0.7,
    conn=None,
) -> tuple[UUID, bool]:
    """Insert or merge a relationship on ``(subject, predicate, object)``.
    Re-assertion bumps ``last_seen_at`` and keeps the higher confidence."""
    pred = (predicate or "").strip().lower()
    async with _acquire(conn) as c:
        row = await c.fetchrow(
            """
            INSERT INTO l1_relationships (subject_id, predicate, object_id, confidence)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (subject_id, predicate, object_id) DO UPDATE SET
                last_seen_at = now(),
                confidence = GREATEST(l1_relationships.confidence, EXCLUDED.confidence)
            RETURNING id, (xmax = 0) AS created
            """,
            subject_id,
            pred,
            object_id,
            float(confidence),
        )
    return row["id"], bool(row["created"])


async def add_citation(
    *,
    slice_id: UUID,
    quote: str = "",
    entity_id: Optional[UUID] = None,
    relationship_id: Optional[UUID] = None,
    conn=None,
) -> UUID:
    """Record that *slice_id* grounded an entity XOR a relationship."""
    if (entity_id is None) == (relationship_id is None):
        raise ValueError("add_citation requires exactly one of entity_id / relationship_id")
    async with _acquire(conn) as c:
        return await c.fetchval(
            """
            INSERT INTO l1_citations (entity_id, relationship_id, slice_id, quote)
            VALUES ($1, $2, $3, $4) RETURNING id
            """,
            entity_id,
            relationship_id,
            slice_id,
            (quote or "")[:500],
        )


async def persist_extraction(result: ParserResult, *, conn) -> list[dict]:
    """Write a Parser extraction (entities → relationships → citations) and
    return the ``consolidated_to`` address list.

    Requires a transaction-bound ``conn`` — the Parser wraps this plus
    :func:`mark_slices_consolidated` in one transaction so a crash can't
    leave half-consolidated state (design §5.7). Relationship endpoints are
    resolved against the entities just upserted; an endpoint the LLM named
    only in a relationship (not in the entities list) is upserted on the
    fly so the FK always resolves.
    """
    addresses: list[dict] = []
    by_key: dict[tuple[str, str], UUID] = {}

    for pe in result.entities:
        etype = normalise_entity_type(pe.entity_type)
        eid, _created = await upsert_entity(
            pe.name, etype, summary=pe.summary, aliases=pe.aliases, conn=conn
        )
        by_key[(pe.name.strip().lower(), etype)] = eid
        for sid in pe.source_slice_ids:
            await add_citation(entity_id=eid, slice_id=sid, quote=pe.quote, conn=conn)
        addresses.append({"layer": "l1", "kind": "entity", "id": str(eid)})

    async def _resolve(name: str, kind: str) -> UUID:
        etype = normalise_entity_type(kind)
        key = (name.strip().lower(), etype)
        if key in by_key:
            return by_key[key]
        eid, _ = await upsert_entity(name, etype, conn=conn)
        by_key[key] = eid
        return eid

    for pr in result.relationships:
        subj = await _resolve(pr.subject_name, pr.subject_type)
        obj = await _resolve(pr.object_name, pr.object_type)
        rid, _created = await upsert_relationship(
            subj, pr.predicate, obj, confidence=pr.confidence, conn=conn
        )
        for sid in pr.source_slice_ids:
            await add_citation(
                relationship_id=rid, slice_id=sid, quote=pr.quote, conn=conn
            )
        addresses.append({"layer": "l1", "kind": "relationship", "id": str(rid)})

    return addresses


async def mark_slices_consolidated(
    slice_ids: list[UUID], l1_addresses: list[dict], *, conn=None
) -> int:
    """Flip ``passed`` slices to ``consolidated`` with their ``consolidated_to``
    address list — the L0 side of the handshake (design §5.7 step 4). A
    slice already ``released`` is left alone (never resurrected). Returns
    the number of slices updated.
    """
    if not slice_ids:
        return 0
    async with _acquire(conn) as c:
        tag = await c.execute(
            """
            UPDATE substrate_slices
               SET consolidation_state = 'consolidated',
                   consolidated_to = $2
             WHERE slice_id = ANY($1::uuid[])
               AND consolidation_state <> 'released'
            """,
            slice_ids,
            l1_addresses,
        )
    # asyncpg returns a command tag like "UPDATE 12".
    try:
        return int(tag.split()[-1])
    except (ValueError, IndexError, AttributeError):
        return 0


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------


async def get_entity_by_id(entity_id: UUID, *, conn=None) -> Optional[Entity]:
    async with _acquire(conn) as c:
        r = await c.fetchrow("SELECT * FROM l1_entities WHERE id = $1", entity_id)
    return _row_to_entity(r) if r else None


async def find_entities_by_name(
    name: str,
    *,
    entity_type: Optional[str] = None,
    fuzzy: bool = True,
    limit: int = 10,
    conn=None,
) -> list[Entity]:
    """Exact or trigram-fuzzy name lookup, optionally constrained by type."""
    async with _acquire(conn) as c:
        if fuzzy:
            rows = await c.fetch(
                """
                SELECT * FROM l1_entities
                 WHERE ($2::text IS NULL OR entity_type = $2)
                   AND (name = $1 OR name % $1)
                 ORDER BY similarity(name, $1) DESC, last_seen_at DESC
                 LIMIT $3
                """,
                name,
                entity_type,
                limit,
            )
        else:
            rows = await c.fetch(
                """
                SELECT * FROM l1_entities
                 WHERE name = $1 AND ($2::text IS NULL OR entity_type = $2)
                 ORDER BY last_seen_at DESC LIMIT $3
                """,
                name,
                entity_type,
                limit,
            )
    return [_row_to_entity(r) for r in rows]


async def get_entities_for_query(
    query: str, *, limit: int = 5, min_salience: float = 0.0, conn=None
) -> list[Entity]:
    """Entities whose name or summary fuzzy-match *query*, ranked by
    similarity + salience. Backs the recall L1 header (Phase D3). Returns
    ``[]`` when L1 is empty or nothing matches."""
    if not (query or "").strip():
        return []
    async with _acquire(conn) as c:
        rows = await c.fetch(
            """
            SELECT *,
                   GREATEST(similarity(name, $1), similarity(COALESCE(summary,''), $1))
                       AS sim
              FROM l1_entities
             WHERE salience_score >= $3
               AND (name % $1 OR summary % $1)
             ORDER BY (GREATEST(similarity(name, $1),
                                similarity(COALESCE(summary,''), $1))
                       + salience_score) DESC,
                      last_seen_at DESC
             LIMIT $2
            """,
            query,
            limit,
            min_salience,
        )
    return [_row_to_entity(r) for r in rows]


async def list_relationships_for_entity(
    entity_id: UUID,
    *,
    direction: Literal["out", "in", "both"] = "both",
    limit: int = 20,
    conn=None,
) -> list[Relationship]:
    if direction == "out":
        where = "subject_id = $1"
    elif direction == "in":
        where = "object_id = $1"
    else:
        where = "(subject_id = $1 OR object_id = $1)"
    async with _acquire(conn) as c:
        rows = await c.fetch(
            f"SELECT * FROM l1_relationships WHERE {where} "
            "ORDER BY last_seen_at DESC LIMIT $2",
            entity_id,
            limit,
        )
    return [_row_to_relationship(r) for r in rows]


async def list_citations_for_entity(
    entity_id: UUID, *, limit: int = 10, conn=None
) -> list[Citation]:
    async with _acquire(conn) as c:
        rows = await c.fetch(
            "SELECT * FROM l1_citations WHERE entity_id = $1 "
            "ORDER BY created_at DESC LIMIT $2",
            entity_id,
            limit,
        )
    return [_row_to_citation(r) for r in rows]


__all__ = [
    "upsert_entity",
    "upsert_relationship",
    "add_citation",
    "persist_extraction",
    "mark_slices_consolidated",
    "get_entity_by_id",
    "find_entities_by_name",
    "get_entities_for_query",
    "list_relationships_for_entity",
    "list_citations_for_entity",
]
