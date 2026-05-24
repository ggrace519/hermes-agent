"""Runtime partition maintenance for ``substrate_slices``.

The Alembic revision ``20260523_0003_substrate_skeleton`` carves out the
current month + 1 month of partitions at migration time and creates a
DEFAULT partition as a safety net. From boot onward this helper keeps a
rolling window of ``current + ahead_months`` partitions present so the
DEFAULT partition stays empty in steady state.

PG 17 propagates indexes from the parent table to every present and
future child, so this module only needs to issue ``CREATE TABLE … IF NOT
EXISTS … PARTITION OF … FOR VALUES FROM (…) TO (…)`` — no per-partition
index DDL.

Drop policy: not in Phase A. Old month partitions accumulate (one per
month is cheap). Curator-driven retention lands with Phase B+.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — import-only for type checkers.
    import asyncpg


def _month_ranges(reference: date, ahead_months: int) -> list[tuple[str, date, date]]:
    """Return ``(partition_name, lo_inclusive, hi_exclusive)`` tuples
    covering ``reference``'s month and the next ``ahead_months`` months.

    Names follow ``substrate_slices_YYYYMM`` so dropping a specific
    month is one ``DROP TABLE`` (retention policy will land with the
    Curator). Mirrors the helper of the same name in the Alembic
    revision — keep them in sync.
    """
    ranges: list[tuple[str, date, date]] = []
    year, month = reference.year, reference.month
    for _ in range(ahead_months + 1):
        lo = date(year, month, 1)
        if month == 12:
            hi = date(year + 1, 1, 1)
            year, month = year + 1, 1
        else:
            hi = date(year, month + 1, 1)
            month += 1
        ranges.append((f"substrate_slices_{lo.year:04d}{lo.month:02d}", lo, hi))
    return ranges


async def ensure_partitions(
    conn: "asyncpg.Connection",
    *,
    ahead_months: int = 2,
    today: date | None = None,
) -> list[str]:
    """Ensure month partitions exist for the current month and the next
    ``ahead_months`` months.

    The default ``ahead_months=2`` matches Phase A spec §3.4.1's rolling
    window. Tests inject ``today`` to make assertions deterministic.

    Returns the list of partition names that were either created by this
    call OR already present after the call — i.e. the names that
    callers can safely route a write to in this run. The DEFAULT
    partition is NOT in this list (it is permanent and not month-keyed).
    """
    reference = today or date.today()
    names: list[str] = []
    for partition_name, lo, hi in _month_ranges(reference, ahead_months):
        # IF NOT EXISTS gives us idempotency without a pre-check
        # round-trip; multiple workers (Phase B+ scaling) can call this
        # concurrently and only one will actually create.
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {partition_name}
                PARTITION OF substrate_slices
                FOR VALUES FROM ('{lo.isoformat()}') TO ('{hi.isoformat()}')
            """
        )
        names.append(partition_name)
    return names


async def list_existing_partitions(conn: "asyncpg.Connection") -> list[str]:
    """Return the names of every child partition of ``substrate_slices``,
    sorted lexicographically (which, by the YYYYMM convention, is also
    chronological).

    Used by the inspect CLI and by tests asserting that partitions were
    created.
    """
    rows = await conn.fetch(
        """
        SELECT child.relname AS name
          FROM pg_inherits  AS i
          JOIN pg_class     AS parent ON parent.oid = i.inhparent
          JOIN pg_class     AS child  ON child.oid  = i.inhrelid
         WHERE parent.relname = 'substrate_slices'
         ORDER BY child.relname
        """
    )
    return [r["name"] for r in rows]
