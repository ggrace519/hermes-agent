"""L4 storage — append-only observations + latest-coherence read."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional
from uuid import UUID

from substrate.l4.schema import Observation


@asynccontextmanager
async def _acquire(conn) -> AsyncIterator[Any]:
    if conn is not None:
        yield conn
        return
    import hermes_db

    async with hermes_db.connection() as fresh:
        yield fresh


def _row(r) -> Observation:
    return Observation(
        id=r["id"],
        kind=r["kind"],
        subject=r["subject"],
        statement=r["statement"],
        score=None if r["score"] is None else float(r["score"]),
        created_at=r["created_at"],
        metadata=r["metadata"] or {},
    )


async def record_observation(
    kind: str,
    subject: str,
    statement: str,
    *,
    score: Optional[float] = None,
    metadata: Optional[dict] = None,
    conn=None,
) -> UUID:
    async with _acquire(conn) as c:
        return await c.fetchval(
            """
            INSERT INTO l4_observations (kind, subject, statement, score, metadata)
            VALUES ($1, $2, $3, $4, $5) RETURNING id
            """,
            kind,
            subject,
            statement,
            None if score is None else float(score),
            metadata or {},
        )


async def list_observations(
    *, subject: Optional[str] = None, kind: Optional[str] = None,
    limit: int = 20, conn=None,
) -> list[Observation]:
    async with _acquire(conn) as c:
        rows = await c.fetch(
            """
            SELECT * FROM l4_observations
             WHERE ($1::text IS NULL OR subject = $1)
               AND ($2::text IS NULL OR kind = $2)
             ORDER BY created_at DESC LIMIT $3
            """,
            subject,
            kind,
            limit,
        )
    return [_row(r) for r in rows]


async def latest_coherence(*, conn=None) -> Optional[Observation]:
    """The most recent ``coherence`` observation — the substrate's current
    self-assessed identity-health vital sign, or None if the Critic hasn't
    run yet."""
    async with _acquire(conn) as c:
        r = await c.fetchrow(
            "SELECT * FROM l4_observations WHERE kind='coherence' "
            "ORDER BY created_at DESC LIMIT 1"
        )
    return _row(r) if r else None


__all__ = ["record_observation", "list_observations", "latest_coherence"]
