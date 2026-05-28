"""Non-perceptual operational-telemetry sink — :func:`write`.

This is where the substrate records its *own* operational decisions
(Conductor dials, Sentinel batch summaries, Curator releases/alarms,
Reflector/Dreamer/Critic/Associator/PatternFinder/Summarizer/Parser
activity, force-reject audits). It is the deliberate counterpart to
``substrate.l0.api.commit_slice``:

* ``commit_slice`` writes **perception** — a slice the awareness loop
  ingests, parses, consolidates, recalls.
* ``telemetry.write`` writes **operational telemetry** — an append-only
  row in ``substrate_telemetry`` that the awareness loop *never reads*.

Why this exists: these events used to be committed as slices on the
perceptual ``substrate.self_state`` stream. Because the Conductor's
backlog forecast counted every ``passed + unconsolidated`` slice — and
audit slices carry no ``session_id`` so the Parser could never drain
them — that closed a self-sustaining feedback loop (2026-05-26→27 prod
incident: 414k ghost slices). Routing them here keeps them out of L0
entirely: no ``awaiting_parse`` increment, no consolidation backlog, no
Curator pending set, never visible to Sentinel/Conductor as input.

The schema-level guard that keeps a *future* component from re-opening
the loop is :func:`substrate.storage.streams.is_perceptual` — anything
on a ``substrate.*`` stream is excluded from awareness-loop queries.
This module is the positive destination for those excluded events.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    import asyncpg
    from substrate.facade import Substrate


_INSERT_SQL = """
    INSERT INTO substrate_telemetry (agent, event, payload, at)
    VALUES ($1, $2, $3, COALESCE($4, now()))
"""


async def write(
    substrate: "Substrate",
    *,
    agent: str,
    event: str,
    payload: Optional[dict] = None,
    at: Optional[datetime] = None,
    conn: "Optional[asyncpg.Connection]" = None,
) -> None:
    """Append one operational-telemetry row. Non-perceptual by design.

    ``agent`` — the emitting sub-agent's ``name`` (e.g. ``"conductor"``).
    ``event`` — the event kind (e.g. ``"conductor.dialed"``).
    ``payload`` — event-specific fields as a JSON-compatible dict. The
        ``event`` kind and the row timestamp are columns, so the payload
        should NOT duplicate them.
    ``at`` — event time; defaults to the PG server clock (``now()``).
    ``conn`` — optional connection to run the INSERT on a caller-owned
        transaction; otherwise a connection is acquired from the pool.

    Unlike ``commit_slice`` this never touches ``substrate_slices`` — so a
    telemetry write can never increment the consolidation backlog, enter
    the Curator's pending set, or be read back as perception. Callers that
    want best-effort semantics (the historical emit sites) should wrap the
    call in their own ``try/except``, matching the prior ``commit_slice``
    audit-emit behaviour.
    """
    row_payload = payload or {}
    if conn is not None:
        await conn.execute(_INSERT_SQL, agent, event, row_payload, at)
        return
    async with substrate.pool.acquire() as own_conn:
        await own_conn.execute(_INSERT_SQL, agent, event, row_payload, at)


__all__ = ["write"]
