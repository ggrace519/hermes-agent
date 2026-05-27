"""L3 storage — async PG helpers over ``l3_patterns``."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional
from uuid import UUID

from substrate.l3.schema import Pattern, normalise_kind


@asynccontextmanager
async def _acquire(conn) -> AsyncIterator[Any]:
    if conn is not None:
        yield conn
        return
    import hermes_db

    async with hermes_db.connection() as fresh:
        yield fresh


def _row_to_pattern(r) -> Pattern:
    return Pattern(
        id=r["id"],
        kind=r["kind"],
        statement=r["statement"],
        cites=list(r["cites"] or []),
        salience_score=float(r["salience_score"]),
        confidence=float(r["confidence"]),
        created_at=r["created_at"],
        last_seen_at=r["last_seen_at"],
        metadata=r["metadata"] or {},
    )


async def upsert_pattern(
    statement: str,
    kind: str,
    *,
    cites: Optional[list[str]] = None,
    confidence: float = 0.5,
    conn=None,
) -> tuple[UUID, bool]:
    """Insert or merge a pattern on ``(kind, statement)``. Re-finding bumps
    salience (capped at 1.0) + last_seen and unions citations. Returns
    ``(pattern_id, created)``."""
    k = normalise_kind(kind)
    statement = (statement or "").strip()
    cites_list = [str(c) for c in (cites or [])]
    async with _acquire(conn) as c:
        row = await c.fetchrow(
            """
            INSERT INTO l3_patterns (kind, statement, cites, confidence)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (kind, statement) DO UPDATE SET
                last_seen_at = now(),
                salience_score = LEAST(1.0, l3_patterns.salience_score + 0.1),
                confidence = GREATEST(l3_patterns.confidence, EXCLUDED.confidence),
                cites = (
                    SELECT jsonb_agg(DISTINCT e)
                      FROM jsonb_array_elements(l3_patterns.cites || EXCLUDED.cites) AS e
                )
            RETURNING id, (xmax = 0) AS created
            """,
            k,
            statement,
            cites_list,
            float(confidence),
        )
    return row["id"], bool(row["created"])


async def get_patterns_for_query(
    query: str, *, limit: int = 5, conn=None
) -> list[Pattern]:
    if not (query or "").strip():
        return []
    async with _acquire(conn) as c:
        rows = await c.fetch(
            """
            SELECT * FROM l3_patterns
             WHERE statement % $1
             ORDER BY (similarity(statement, $1) + salience_score) DESC,
                      last_seen_at DESC
             LIMIT $2
            """,
            query,
            limit,
        )
    return [_row_to_pattern(r) for r in rows]


async def list_patterns(
    *, kind: Optional[str] = None, limit: int = 20, conn=None
) -> list[Pattern]:
    async with _acquire(conn) as c:
        rows = await c.fetch(
            """
            SELECT * FROM l3_patterns
             WHERE ($1::text IS NULL OR kind = $1)
             ORDER BY salience_score DESC, last_seen_at DESC
             LIMIT $2
            """,
            kind,
            limit,
        )
    return [_row_to_pattern(r) for r in rows]


__all__ = ["upsert_pattern", "get_patterns_for_query", "list_patterns"]
