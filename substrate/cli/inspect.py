"""``hermes substrate`` — substrate state inspection commands.

Surface (flattened from earlier ``hermes substrate inspect <thing>``;
the redundant ``inspect`` verb was removed 2026-05-26):

    hermes substrate                       # default summary
    hermes substrate streams               # list streams + slice counts
    hermes substrate slices --stream NAME --limit 20
    hermes substrate pending               # current pending-queue depth
    hermes substrate profiles              # decay profiles
    hermes substrate curator [SUB]         # Curator subtree (Phase B)
    hermes substrate recall  [SUB]         # Recall subtree (Phase C)

The CLI does not boot the full substrate (no sub-agent loops) — it just
initialises the asyncpg pool, runs read-only queries against the substrate
tables, and prints a fixed-format report. Safe to run against a Hermes
deployment that is already booted in another process.

Wired into Hermes's top-level argparse via :func:`register_subparser`
called from ``hermes_cli/main.py``. Mutating/admin operations on
embeddings live under the separate ``hermes embed`` namespace; see
``substrate/cli/embed.py``.
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
        help="Inspect substrate state (streams, slices, curator, recall)",
        description="Read-only inspection of the substrate's perception "
        "streams, slices, decay profiles, pending queue, and the Curator + "
        "recall subsystems. With no subcommand, prints the default summary. "
        "Embedding admin (reshape, backfill) lives under ``hermes embed``.",
    )
    substrate_sub = substrate_parser.add_subparsers(dest="substrate_command")
    # Default for ``hermes substrate`` with no subcommand: print the summary
    # (the same content the old ``hermes substrate inspect`` printed).
    substrate_parser.set_defaults(func=_cmd_inspect_summary)

    substrate_sub.add_parser(
        "streams", help="List streams + per-stream slice counts"
    ).set_defaults(func=_cmd_inspect_streams)

    slices_p = substrate_sub.add_parser(
        "slices", help="List the most-recent N slices on a given stream"
    )
    slices_p.add_argument(
        "--stream",
        required=True,
        help="Stream name (e.g. hermes.world.user_message.cli)",
    )
    slices_p.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max number of slices to show (default 20)",
    )
    slices_p.set_defaults(func=_cmd_inspect_slices)

    substrate_sub.add_parser(
        "pending", help="Show pending-queue depth + oldest entry age"
    ).set_defaults(func=_cmd_inspect_pending)

    substrate_sub.add_parser(
        "profiles", help="List decay profiles"
    ).set_defaults(func=_cmd_inspect_profiles)

    # ── Phase B: curator subtree ──────────────────────────────────────
    curator_p = substrate_sub.add_parser(
        "curator",
        help="Inspect Curator state",
        description="Show Curator decay/release activity. Without a sub "
        "subcommand, prints the default summary.",
    )
    curator_sub = curator_p.add_subparsers(dest="curator_subcommand")
    curator_p.set_defaults(func=_cmd_inspect_curator_summary)

    curator_sub.add_parser(
        "summary", help="Curator summary (default)"
    ).set_defaults(func=_cmd_inspect_curator_summary)

    curator_sub.add_parser(
        "histogram", help="Per-profile salience histogram (10 buckets)"
    ).set_defaults(func=_cmd_inspect_curator_histogram)

    curator_recent = curator_sub.add_parser(
        "recent", help="Recent curator.* self-state emissions"
    )
    curator_recent.add_argument(
        "--limit", type=int, default=20, help="Max emissions to show (default 20)"
    )
    curator_recent.set_defaults(func=_cmd_inspect_curator_recent)

    curator_sub.add_parser(
        "pressure",
        help="Per-stream salience pressure (Conductor opportunity-forecast inputs)",
    ).set_defaults(func=_cmd_inspect_curator_pressure)

    # ── Phase C: recall subtree ───────────────────────────────────────
    recall_p = substrate_sub.add_parser(
        "recall",
        help="Inspect recall pipeline state",
        description="Show recent recall calls + embedding coverage + config.",
    )
    recall_sub = recall_p.add_subparsers(dest="recall_subcommand")
    recall_p.set_defaults(func=_cmd_inspect_recall_summary)

    recall_sub.add_parser(
        "summary", help="Recall summary (default) — last 1h call stats + coverage"
    ).set_defaults(func=_cmd_inspect_recall_summary)

    recall_recent = recall_sub.add_parser(
        "recent", help="Recent recall calls (substrate_recall_log)"
    )
    recall_recent.add_argument(
        "--limit", type=int, default=20, help="Max log rows to show (default 20)"
    )
    recall_recent.set_defaults(func=_cmd_inspect_recall_recent)

    recall_sample = recall_sub.add_parser(
        "sample", help="Last recall log row for a given session"
    )
    recall_sample.add_argument(
        "--session-id", required=True, help="Hermes session id"
    )
    recall_sample.set_defaults(func=_cmd_inspect_recall_sample)

    recall_sub.add_parser(
        "config", help="Dump RECALL_* config knobs"
    ).set_defaults(func=_cmd_inspect_recall_config)

    # ── Sub-agent worker subprocess ────────────────────────────────────
    # ``hermes substrate worker run`` blocks while running Sentinel +
    # Curator + ForceRejectWorker + PartitionMaintenanceWorker in a
    # dedicated process. Managed by the systemd unit
    # ``hermes-substrate-worker.service``; rare for operators to invoke
    # by hand. Lives in its own module because the run loop is meaty.
    from substrate.cli import worker as _worker_mod

    _worker_mod.register_subparser(substrate_sub)


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


def _cmd_inspect_curator_summary(args: argparse.Namespace) -> int:
    return _run_inspect(_print_curator_summary)


def _cmd_inspect_curator_histogram(args: argparse.Namespace) -> int:
    return _run_inspect(_print_curator_histogram)


def _cmd_inspect_curator_recent(args: argparse.Namespace) -> int:
    return _run_inspect(
        lambda conn: _print_curator_recent(conn, limit=args.limit)
    )


def _cmd_inspect_curator_pressure(args: argparse.Namespace) -> int:
    return _run_inspect(_print_curator_pressure)


# ── Phase C: recall command dispatchers ──────────────────────────────


def _cmd_inspect_recall_summary(args: argparse.Namespace) -> int:
    from substrate.recall.cli_inspect import print_summary

    return _run_inspect(print_summary)


def _cmd_inspect_recall_recent(args: argparse.Namespace) -> int:
    from substrate.recall.cli_inspect import print_recent

    return _run_inspect(lambda conn: print_recent(conn, limit=args.limit))


def _cmd_inspect_recall_sample(args: argparse.Namespace) -> int:
    from substrate.recall.cli_inspect import print_sample

    return _run_inspect(
        lambda conn: print_sample(conn, session_id=args.session_id)
    )


def _cmd_inspect_recall_config(args: argparse.Namespace) -> int:
    from substrate.recall.cli_inspect import print_config

    return _run_inspect(print_config)


def _run_inspect(action) -> int:
    """Shared boilerplate: ensure the pool, acquire a connection, hand it
    to the printer. Closes the pool on exit so the CLI doesn't leave a
    dangling asyncpg pool behind.
    """
    import hermes_db

    if not hermes_db.ensure_pool_sync():
        print(
            "error: HERMES_PG_DSN is not set and no pool is initialised; "
            "configure it before running `hermes substrate`.",
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
    # wanting live intensity should check the Hermes process logs.
    print("   sentinel        FULL    (see process logs)")
    print("   force-reject    LOW     (see process logs)")
    print("   partition-maintenance FULL (24h tick)")
    print("   conductor       —       (no policy active)")


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


# ---------------------------------------------------------------------------
# Phase B printers — Curator summary, histogram, recent, pressure.
# ---------------------------------------------------------------------------


async def _print_curator_summary(conn: "asyncpg.Connection") -> None:
    """Default ``hermes substrate curator`` output. Matches the
    format documented in Phase B spec §9.2."""
    now = datetime.now(timezone.utc)
    print(f"Curator state @ {now.isoformat()}")
    print()

    # Release stats — count by tombstone_policy. Released slices keep
    # their consolidation_state='released' even after payload is nulled.
    rel = await conn.fetch(
        """
        SELECT dp.tombstone_policy AS policy, COUNT(sl.slice_id)::int AS n
          FROM substrate_slices sl
          JOIN substrate_streams st ON st.stream_id = sl.stream_id
          JOIN substrate_decay_profiles dp ON dp.profile_id = st.decay_profile_id
         WHERE sl.consolidation_state = 'released'
         GROUP BY dp.tombstone_policy
        """
    )
    rel_by_policy = {r["policy"]: r["n"] for r in rel}
    rel_total = sum(rel_by_policy.values())
    print(f"Released:                   {rel_total:,} slices")
    print("  by policy:")
    for policy in ("thin", "full", "none"):
        label = policy + (" (default)" if policy == "thin" else "")
        print(f"    {label:24s}{rel_by_policy.get(policy, 0):,}")

    print()

    # Pending consolidation — anything passed + unconsolidated.
    pending = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (
                WHERE sl.sentinel_state = 'passed'
                  AND sl.consolidation_state = 'unconsolidated'
            )::int AS total,
            COUNT(*) FILTER (
                WHERE sl.sentinel_state = 'passed'
                  AND sl.consolidation_state = 'unconsolidated'
                  AND sl.salience_score < 0.1
            )::int AS low_salience,
            COUNT(*) FILTER (
                WHERE sl.sentinel_state = 'passed'
                  AND sl.consolidation_state = 'unconsolidated'
                  AND sl.ingest_time_world + (dp.consolidation_window * 0.9) < now()
                  AND sl.ingest_time_world + dp.consolidation_window > now()
            )::int AS near_window
          FROM substrate_slices sl
          JOIN substrate_streams st ON st.stream_id = sl.stream_id
          JOIN substrate_decay_profiles dp ON dp.profile_id = st.decay_profile_id
        """
    )
    print(f"Pending consolidation:        {pending['total']:,} slices")
    print(f"  with salience < 0.1            {pending['low_salience']:,}")
    print(
        f"  approaching consolidation_window (within 10% of profile setting): "
        f"{pending['near_window']:,}"
    )

    print()

    # Recent curator emissions over last hour. Counts per event kind.
    recent = await conn.fetch(
        """
        SELECT sl.payload->>'event' AS event, COUNT(*)::int AS n
          FROM substrate_slices sl
          JOIN substrate_streams st ON st.stream_id = sl.stream_id
         WHERE st.name = 'substrate.self_state'
           AND sl.ingest_time_world > now() - interval '1 hour'
           AND sl.payload->>'event' LIKE 'curator.%'
         GROUP BY sl.payload->>'event'
        """
    )
    recent_by_event = {r["event"]: r["n"] for r in recent}
    rel_n = recent_by_event.get("curator.release", 0)
    alarm_n = recent_by_event.get(
        "curator.pathological_forgetting_alarm", 0
    )
    print(f"Recent curator.release emissions: {rel_n} in last hour")
    print(f"Recent pathological-forgetting alarms: {alarm_n} in last hour")


async def _print_curator_histogram(conn: "asyncpg.Connection") -> None:
    """10-bucket salience histogram per active profile.

    Bucket 0 covers [0.0, 0.1); bucket 9 covers [0.9, 1.0]. Released
    slices are excluded (their salience is 0 by definition and would
    dominate bucket 0).
    """
    rows = await conn.fetch(
        """
        WITH bucketed AS (
            SELECT dp.name AS profile,
                   LEAST(9, GREATEST(0, FLOOR(sl.salience_score * 10)::int)) AS bucket
              FROM substrate_slices sl
              JOIN substrate_streams st ON st.stream_id = sl.stream_id
              JOIN substrate_decay_profiles dp ON dp.profile_id = st.decay_profile_id
             WHERE sl.consolidation_state <> 'released'
               AND sl.sentinel_state = 'passed'
        )
        SELECT profile, bucket, COUNT(*)::int AS n
          FROM bucketed
         GROUP BY profile, bucket
         ORDER BY profile, bucket
        """
    )
    if not rows:
        print("(no active passed slices to bucket)")
        return

    by_profile: dict[str, dict[int, int]] = {}
    for r in rows:
        by_profile.setdefault(r["profile"], {})[r["bucket"]] = r["n"]

    bucket_labels = [
        "0.0–0.1", "0.1–0.2", "0.2–0.3", "0.3–0.4", "0.4–0.5",
        "0.5–0.6", "0.6–0.7", "0.7–0.8", "0.8–0.9", "0.9–1.0",
    ]
    for profile, buckets in by_profile.items():
        total = sum(buckets.values())
        print(f"{profile}  (total = {total})")
        for i, label in enumerate(bucket_labels):
            n = buckets.get(i, 0)
            bar = "█" * min(50, n)
            print(f"  {label}  {n:>6d}  {bar}")
        print()


async def _print_curator_recent(
    conn: "asyncpg.Connection", *, limit: int
) -> None:
    """Most-recent N curator.* emissions on substrate.self_state."""
    rows = await conn.fetch(
        """
        SELECT sl.ingest_time_world, sl.payload
          FROM substrate_slices sl
          JOIN substrate_streams st ON st.stream_id = sl.stream_id
         WHERE st.name = 'substrate.self_state'
           AND sl.payload->>'event' LIKE 'curator.%'
         ORDER BY sl.ingest_time_world DESC
         LIMIT $1
        """,
        limit,
    )
    if not rows:
        print("(no curator emissions found)")
        return
    print(f"Most-recent {len(rows)} curator emissions:")
    for r in rows:
        ev = r["payload"].get("event", "?")
        ts = r["ingest_time_world"].isoformat() if r["ingest_time_world"] else "-"
        if ev == "curator.release":
            extra = (
                f"slice={r['payload'].get('slice_id', '?')[:8]} "
                f"policy={r['payload'].get('tombstone_policy')} "
                f"salience={r['payload'].get('salience_at_release'):.4f}"
            )
        elif ev == "curator.pathological_forgetting_alarm":
            extra = (
                f"slice={r['payload'].get('slice_id', '?')[:8]} "
                f"age={r['payload'].get('age_seconds')}s "
                f"window={r['payload'].get('consolidation_window_seconds')}s "
                f"bumped→{r['payload'].get('bumped_to'):.4f}"
            )
        else:
            extra = str(r["payload"])
        print(f"  [{ts}] {ev}  {extra}")


async def _print_curator_pressure(conn: "asyncpg.Connection") -> None:
    """Per-stream salience pressure — density + update rate.

    Surfaces design §5.6's Conductor opportunity-forecast inputs. Phase
    B doesn't consume programmatically; the values are surfaced so
    operators can develop intuition before Phase F's real Conductor.
    """
    rows = await conn.fetch(
        """
        SELECT st.name,
               COALESCE(AVG(sl.salience_score), 0)::real AS density,
               COUNT(sl.slice_id)::int AS count,
               COUNT(*) FILTER (
                   WHERE sl.salience_updated_at > now() - interval '5 minutes'
               )::int AS update_rate
          FROM substrate_streams st
          LEFT JOIN substrate_slices sl
                 ON sl.stream_id = st.stream_id
                AND sl.consolidation_state <> 'released'
         WHERE st.lifecycle_state = 'active'
         GROUP BY st.name
         ORDER BY st.name
        """
    )
    if not rows:
        print("(no active streams)")
        return
    print(
        f"{'stream':50s}  {'count':>8s}  {'density':>9s}  {'5m_updates':>11s}"
    )
    print("-" * 90)
    for r in rows:
        print(
            f"{r['name']:50s}  {r['count']:>8d}  "
            f"{r['density']:>9.4f}  {r['update_rate']:>11d}"
        )


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
