"""``hermes substrate inspect`` — Phase A debug subcommand.

Surface (per spec §10):

    hermes substrate inspect              # default summary
    hermes substrate inspect streams      # list streams + slice counts
    hermes substrate inspect slices --stream NAME --limit 20
    hermes substrate inspect pending      # current pending-queue depth
    hermes substrate inspect profiles     # decay profiles

The CLI does not boot the full substrate (no sub-agent loops) — it just
initialises the asyncpg pool, runs read-only queries against the substrate
tables, and prints a fixed-format report. This keeps the inspect command
safe to run against a Hermes deployment that is already booted in another
process.

Wired into Hermes's top-level argparse via :func:`register_subparser`
called from ``hermes_cli/main.py``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    import asyncpg


# ---------------------------------------------------------------------------
# Subparser registration — called from hermes_cli/main.py.
# ---------------------------------------------------------------------------


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Add the ``hermes substrate`` subcommand tree to ``subparsers``.

    Matches the pattern used by other optional CLI modules (e.g.
    ``agent.lsp.cli.register_subparser``) so the import is contained and
    a failure to register doesn't break the rest of the CLI.
    """
    substrate_parser = subparsers.add_parser(
        "substrate",
        help="Cognitive substrate debug surface (Phase A)",
        description="Inspect the substrate's perception streams, slices, "
        "decay profiles, and pending queue.",
    )
    substrate_sub = substrate_parser.add_subparsers(dest="substrate_command")

    inspect_parser = substrate_sub.add_parser(
        "inspect",
        help="Inspect substrate state",
        description="Print a summary of substrate state. Without an argument, "
        "prints the default summary (streams, slice counts, pending queue).",
    )
    inspect_sub = inspect_parser.add_subparsers(dest="inspect_command")
    inspect_parser.set_defaults(func=_cmd_inspect_summary)

    inspect_streams = inspect_sub.add_parser(
        "streams", help="List streams + per-stream slice counts"
    )
    inspect_streams.set_defaults(func=_cmd_inspect_streams)

    inspect_slices = inspect_sub.add_parser(
        "slices", help="List the most-recent N slices on a given stream"
    )
    inspect_slices.add_argument(
        "--stream",
        required=True,
        help="Stream name (e.g. hermes.world.user_message.cli)",
    )
    inspect_slices.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max number of slices to show (default 20)",
    )
    inspect_slices.set_defaults(func=_cmd_inspect_slices)

    inspect_pending = inspect_sub.add_parser(
        "pending", help="Show pending-queue depth + oldest entry age"
    )
    inspect_pending.set_defaults(func=_cmd_inspect_pending)

    inspect_profiles = inspect_sub.add_parser(
        "profiles", help="List decay profiles"
    )
    inspect_profiles.set_defaults(func=_cmd_inspect_profiles)

    substrate_parser.set_defaults(func=_cmd_substrate_help)


def _cmd_substrate_help(args: argparse.Namespace) -> int:
    """Default for ``hermes substrate`` with no subcommand."""
    print(
        "usage: hermes substrate inspect [streams|slices|pending|profiles]",
        file=sys.stderr,
    )
    return 2


# ---------------------------------------------------------------------------
# Command handlers — each one is sync and bridges via hermes_db.run_sync.
# This matches the rest of the Hermes CLI, where command callbacks are
# synchronous and call asyncio.run / run_sync as needed.
# ---------------------------------------------------------------------------


def _cmd_inspect_summary(args: argparse.Namespace) -> int:
    return _run_inspect(_print_summary)


def _cmd_inspect_streams(args: argparse.Namespace) -> int:
    return _run_inspect(_print_streams)


def _cmd_inspect_slices(args: argparse.Namespace) -> int:
    return _run_inspect(
        lambda conn: _print_slices(conn, stream_name=args.stream, limit=args.limit)
    )


def _cmd_inspect_pending(args: argparse.Namespace) -> int:
    return _run_inspect(_print_pending)


def _cmd_inspect_profiles(args: argparse.Namespace) -> int:
    return _run_inspect(_print_profiles)


def _run_inspect(action) -> int:
    """Shared boilerplate: ensure the pool, acquire a connection, hand it
    to the printer. Closes the pool on exit so the CLI doesn't leave a
    dangling asyncpg pool behind.
    """
    import hermes_db

    if not hermes_db.ensure_pool_sync():
        print(
            "error: HERMES_PG_DSN is not set and no pool is initialised; "
            "configure it before running `hermes substrate inspect`.",
            file=sys.stderr,
        )
        return 1

    async def _go() -> int:
        async with hermes_db.connection() as conn:
            try:
                await action(conn)
            except Exception as exc:  # pragma: no cover — defensive
                print(f"error: {exc}", file=sys.stderr)
                return 1
        return 0

    try:
        return hermes_db.run_sync(_go())
    finally:
        # The Hermes CLI process is one-shot; release the pool so it
        # doesn't hold a connection open past the subcommand's exit.
        try:
            hermes_db.run_sync(hermes_db.close())
        except Exception:  # pragma: no cover — close is best-effort
            pass


# ---------------------------------------------------------------------------
# Printers — each takes an asyncpg.Connection and prints to stdout.
# ---------------------------------------------------------------------------


async def _print_summary(conn: "asyncpg.Connection") -> None:
    """Top-level summary — the format documented in spec §10.2."""
    now = datetime.now(timezone.utc)
    print(f"Substrate state @ {now.isoformat()}")
    print()

    streams = await _stream_counts(conn)
    n_total = len(streams)
    n_active = sum(1 for s in streams if s["lifecycle_state"] == "active")
    n_paused = sum(1 for s in streams if s["lifecycle_state"] == "paused")
    n_retired = sum(1 for s in streams if s["lifecycle_state"] == "retired")
    print(
        f"Streams: {n_total} registered "
        f"({n_active} active, {n_paused} paused, {n_retired} retired)"
    )

    slice_totals = await _slice_state_counts(conn)
    total_slices = sum(slice_totals.values())
    print(f"Slices:  {total_slices:,} total")
    for state in ("pending", "passed", "quarantined"):
        print(f"   {state:11s} {slice_totals.get(state, 0):,}")

    print()
    pending = await _pending_info(conn)
    print("Pending queue:")
    print(f"   depth = {pending['depth']}")
    if pending["oldest_age"] is not None:
        secs = pending["oldest_age"].total_seconds()
        print(f"   oldest pending = {secs:.1f}s ago")
    else:
        print("   oldest pending = (empty)")

    print()
    print("Sub-agents (intensity):")
    # Sub-agent state lives in-process; the CLI doesn't have a handle to
    # the booted Substrate. Print the static expected list — operators
    # wanting live intensity should run `hermes substrate inspect`
    # against a Hermes process via an admin surface (Phase B+).
    print("   sentinel        FULL    (Phase A stub — see process logs)")
    print("   force-reject    LOW     (Phase A — see process logs)")
    print("   partition-maintenance FULL (24h tick)")
    print("   conductor       —       (Phase A stub: no policy)")


async def _print_streams(conn: "asyncpg.Connection") -> None:
    streams = await _stream_counts(conn)
    if not streams:
        print("(no streams registered)")
        return
    print(f"{'name':50s}  {'family':14s}  {'modality':17s}  {'state':10s}  count")
    print("-" * 110)
    for s in streams:
        print(
            f"{s['name']:50s}  {s['family']:14s}  {s['modality']:17s}  "
            f"{s['lifecycle_state']:10s}  {s['slice_count']:>6d}"
        )


async def _print_slices(
    conn: "asyncpg.Connection", *, stream_name: str, limit: int
) -> None:
    rows = await conn.fetch(
        """
        SELECT sl.slice_id, sl.sentinel_state, sl.payload_modality,
               sl.event_time_world, sl.payload, sl.metadata
          FROM substrate_slices sl
          JOIN substrate_streams st ON st.stream_id = sl.stream_id
         WHERE st.name = $1
         ORDER BY sl.event_time_world DESC
         LIMIT $2
        """,
        stream_name,
        limit,
    )
    if not rows:
        print(f"(no slices for stream {stream_name!r})")
        return
    print(f"Most-recent {len(rows)} slices for {stream_name!r}:")
    for r in rows:
        ev = r["event_time_world"].isoformat() if r["event_time_world"] else "-"
        sid = str(r["slice_id"])
        state = r["sentinel_state"]
        modality = r["payload_modality"]
        payload_preview = _short_payload(r["payload"])
        print(f"  [{ev}] {sid}  {state:11s}  {modality:17s}  {payload_preview}")


async def _print_pending(conn: "asyncpg.Connection") -> None:
    info = await _pending_info(conn)
    print(f"depth: {info['depth']}")
    if info["oldest_age"] is not None:
        print(f"oldest age: {info['oldest_age'].total_seconds():.2f}s")
    else:
        print("oldest age: (no pending slices)")


async def _print_profiles(conn: "asyncpg.Connection") -> None:
    rows = await conn.fetch(
        """
        SELECT name, natural_half_life, consolidation_window,
               pending_ttl, tombstone_policy, applies_to_modality
          FROM substrate_decay_profiles
         ORDER BY name
        """
    )
    if not rows:
        print("(no decay profiles seeded — migration may not have run)")
        return
    print(
        f"{'name':22s}  {'half_life':12s}  {'consolidation':15s}  "
        f"{'pending_ttl':12s}  {'tombstone':10s}  modality"
    )
    print("-" * 100)
    for r in rows:
        print(
            f"{r['name']:22s}  "
            f"{_fmt_td(r['natural_half_life']):12s}  "
            f"{_fmt_td(r['consolidation_window']):15s}  "
            f"{_fmt_td(r['pending_ttl']):12s}  "
            f"{r['tombstone_policy']:10s}  "
            f"{r['applies_to_modality'] or '-'}"
        )


# ---------------------------------------------------------------------------
# Query helpers shared across printers.
# ---------------------------------------------------------------------------


async def _stream_counts(conn: "asyncpg.Connection") -> list[dict]:
    """Streams joined with their slice counts, ordered by name. Empty
    streams (no slices yet) still appear with count 0."""
    rows = await conn.fetch(
        """
        SELECT st.stream_id, st.name, st.family, st.modality,
               st.lifecycle_state,
               COUNT(sl.slice_id) AS slice_count
          FROM substrate_streams st
          LEFT JOIN substrate_slices sl ON sl.stream_id = st.stream_id
         GROUP BY st.stream_id
         ORDER BY st.name
        """
    )
    return [dict(r) for r in rows]


async def _slice_state_counts(conn: "asyncpg.Connection") -> dict[str, int]:
    """``sentinel_state → count`` across all slices."""
    rows = await conn.fetch(
        """
        SELECT sentinel_state, COUNT(*) AS n
          FROM substrate_slices
         GROUP BY sentinel_state
        """
    )
    return {r["sentinel_state"]: int(r["n"]) for r in rows}


async def _pending_info(conn: "asyncpg.Connection") -> dict:
    """Depth of the pending queue + oldest pending age (or None)."""
    row = await conn.fetchrow(
        """
        SELECT COUNT(*)::int AS depth,
               MIN(pending_committed_at) AS oldest
          FROM substrate_slices
         WHERE sentinel_state = 'pending'
        """
    )
    depth = int(row["depth"] or 0)
    oldest_age: Optional[timedelta] = None
    if depth > 0 and row["oldest"] is not None:
        oldest_age = datetime.now(timezone.utc) - row["oldest"]
    return {"depth": depth, "oldest_age": oldest_age}


def _short_payload(payload) -> str:
    """One-line payload preview for the slices printer."""
    if payload is None:
        return "(no payload — blob ref?)"
    s = str(payload)
    if len(s) > 80:
        return s[:80] + "…"
    return s


def _fmt_td(value) -> str:
    """Format a timedelta as a short ``2h15m`` / ``45s`` / ``500ms`` style.

    Used by the profiles printer where INTERVAL columns come back as
    :class:`timedelta` (asyncpg native).
    """
    if value is None:
        return "-"
    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds())
        if total_seconds == 0:
            ms = int(value.microseconds / 1000)
            return f"{ms}ms"
        if total_seconds < 60:
            return f"{total_seconds}s"
        if total_seconds < 3600:
            return f"{total_seconds // 60}m"
        hours = total_seconds // 3600
        mins = (total_seconds % 3600) // 60
        if mins:
            return f"{hours}h{mins}m"
        return f"{hours}h"
    return str(value)


__all__ = ["register_subparser"]
