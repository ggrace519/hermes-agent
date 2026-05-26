"""``hermes substrate recall`` printers — Phase C Task 12.

Implementation lives next to the recall API for cohesion; the parent
``substrate/cli/inspect.py`` registers the subparser and delegates the
print to the functions here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    import asyncpg


async def print_summary(conn: "asyncpg.Connection") -> None:
    """Default ``recall`` subcommand output (spec §8.2)."""
    from substrate.storage.slices import SliceRepo

    # recall_stats only uses the passed-in conn — pool can be None for
    # the throwaway repo used here. The real repo lives on the booted
    # Substrate; the inspect CLI deliberately doesn't boot the substrate
    # (it's a read-only debug surface).
    repo = SliceRepo(pool=None)
    stats = await repo.recall_stats(conn, window=timedelta(hours=1))
    now = datetime.now(timezone.utc)
    print(f"Recall state @ {now.isoformat()}")
    print()

    total = int(stats.get("total_calls", 0) or 0)
    non_empty = int(stats.get("non_empty_calls", 0) or 0)
    timed_out = int(stats.get("timed_out_calls", 0) or 0)
    errors = int(stats.get("error_calls", 0) or 0)
    pct = (100 * non_empty / total) if total else 0
    print("Last 1 hour:")
    print(f"  calls           {total}")
    print(f"  non-empty       {non_empty} ({pct:.0f}%)")
    print(f"  timed-out       {timed_out}")
    print(f"  errors          {errors}")
    print(f"  avg duration   {int(stats.get('avg_duration_ms', 0) or 0)} ms")
    print(f"  avg tokens     {int(stats.get('avg_tokens', 0) or 0)}")
    print(
        f"  avg candidates {int(stats.get('avg_candidates', 0) or 0)} / call, "
        f"{int(stats.get('avg_composed', 0) or 0)} composed"
    )
    print()

    total_slices = int(stats.get("total_slices", 0) or 0)
    embedded = int(stats.get("embedded_slices", 0) or 0)
    backlog = int(stats.get("unembedded_backlog", 0) or 0)
    coverage = (100 * embedded / total_slices) if total_slices else 0
    print("Embedding coverage:")
    print(f"  total slices              {total_slices}")
    print(f"  with embedding            {embedded} ({coverage:.1f}%)")
    print(f"  unembedded (backlog)      {backlog}")

    semantic = int(stats.get("semantic_path_count", 0) or 0)
    keyword = int(stats.get("keyword_path_count", 0) or 0)
    mixed = int(stats.get("mixed_path_count", 0) or 0)
    path_total = semantic + keyword + mixed
    if path_total:
        sem_pct = 100 * semantic / path_total
        kw_pct = 100 * keyword / path_total
        mx_pct = 100 * mixed / path_total
        print(
            f"  ranking path (last hour)  semantic {sem_pct:.0f}%, "
            f"keyword {kw_pct:.0f}%, mixed {mx_pct:.0f}%"
        )
    print()

    import os

    enabled = os.environ.get("HERMES_SUBSTRATE_RECALL", "1")
    print("Provider status:")
    print(f"  HERMES_SUBSTRATE_RECALL = {enabled}")


async def print_recent(conn: "asyncpg.Connection", *, limit: int) -> None:
    """Recent recall calls — table view from substrate_recall_log."""
    rows = await conn.fetch(
        """
        SELECT log_id, requested_at, session_id, query_excerpt,
               candidates_count, composed_count, tokens_used,
               duration_ms, timed_out, error_text,
               metadata->>'embedding_path' AS embedding_path
          FROM substrate_recall_log
         ORDER BY requested_at DESC
         LIMIT $1
        """,
        limit,
    )
    if not rows:
        print("(no recall log rows)")
        return
    # Header
    print(
        f"{'id':>6}  {'when':<25}  {'sess':<14}  {'cand':>4}  "
        f"{'comp':>4}  {'toks':>5}  {'ms':>4}  {'path':<8}  query"
    )
    for r in rows:
        when = r["requested_at"].isoformat(timespec="seconds")
        sess = (r["session_id"] or "")[:14]
        path = (r["embedding_path"] or "-")[:8]
        excerpt = (r["query_excerpt"] or "")[:50]
        flag = "T" if r["timed_out"] else "-"
        print(
            f"{r['log_id']:>6}  {when:<25}  {sess:<14}  "
            f"{r['candidates_count']:>4}  {r['composed_count']:>4}  "
            f"{r['tokens_used']:>5}  {r['duration_ms']:>4}  "
            f"{path:<8}  {flag} {excerpt}"
        )


async def print_sample(conn: "asyncpg.Connection", *, session_id: str) -> None:
    """Last log row for a session — useful for the operator to verify a
    given session is actually producing recall output."""
    row = await conn.fetchrow(
        """
        SELECT *
          FROM substrate_recall_log
         WHERE session_id = $1
         ORDER BY requested_at DESC
         LIMIT 1
        """,
        session_id,
    )
    if row is None:
        print(f"(no recall log rows for session_id={session_id!r})")
        return
    print(f"session_id:     {row['session_id']}")
    print(f"requested_at:   {row['requested_at'].isoformat()}")
    print(f"query_excerpt:  {row['query_excerpt']}")
    print(f"candidates:     {row['candidates_count']}")
    print(f"composed:       {row['composed_count']}")
    print(f"tokens_used:    {row['tokens_used']}")
    print(f"duration_ms:    {row['duration_ms']}")
    print(f"timed_out:      {row['timed_out']}")
    print(f"error_text:     {row['error_text'] or '-'}")
    print(f"metadata:       {row['metadata']}")


async def print_config(conn: "asyncpg.Connection") -> None:
    """Dump the current RECALL_* config knobs."""
    from substrate import config as _cfg

    print("Phase C recall config:")
    print(f"  RECALL_TOKEN_BUDGET                   = {_cfg.RECALL_TOKEN_BUDGET}")
    print(f"  RECALL_TIME_WINDOW_HOURS              = {_cfg.RECALL_TIME_WINDOW_HOURS}")
    print(f"  RECALL_TIMEOUT_MS                     = {_cfg.RECALL_TIMEOUT_MS}")
    print(f"  RECALL_MIN_SALIENCE                   = {_cfg.RECALL_MIN_SALIENCE}")
    print(f"  RECALL_CANDIDATE_LIMIT                = {_cfg.RECALL_CANDIDATE_LIMIT}")
    print(f"  RECALL_SIMILARITY_WEIGHT              = {_cfg.RECALL_SIMILARITY_WEIGHT}")
    print(f"  RECALL_KEYWORD_WEIGHT                 = {_cfg.RECALL_KEYWORD_WEIGHT}")
    print(f"  RECALL_SALIENCE_WEIGHT                = {_cfg.RECALL_SALIENCE_WEIGHT}")
    print(f"  RECALL_RECENCY_WEIGHT                 = {_cfg.RECALL_RECENCY_WEIGHT}")
    print(f"  RECALL_RECENCY_HALF_LIFE_HOURS        = {_cfg.RECALL_RECENCY_HALF_LIFE_HOURS}")
    print(f"  RECALL_REINFORCE_RATE_LIMIT_PER_MIN   = {_cfg.RECALL_REINFORCE_RATE_LIMIT_PER_MIN}")
    print(f"  RECALL_LOG_QUEUE_DEPTH                = {_cfg.RECALL_LOG_QUEUE_DEPTH}")
    print(f"  RECALL_EMBEDDING_MODEL                = {_cfg.RECALL_EMBEDDING_MODEL!r}")
    print(f"  RECALL_EMBEDDING_DIM                  = {_cfg.RECALL_EMBEDDING_DIM}")
    print(f"  RECALL_EMBEDDING_TIMEOUT_MS           = {_cfg.RECALL_EMBEDDING_TIMEOUT_MS}")
    print(f"  RECALL_EMBEDDING_QUEUE_DEPTH          = {_cfg.RECALL_EMBEDDING_QUEUE_DEPTH}")
    print(f"  RECALL_EMBEDDING_BATCH_SIZE           = {_cfg.RECALL_EMBEDDING_BATCH_SIZE}")
    print(f"  RECALL_EMBEDDING_BACKFILL_INTERVAL_S  = {_cfg.RECALL_EMBEDDING_BACKFILL_INTERVAL_S}")
    print(f"  RECALL_EMBEDDING_BACKFILL_MAX_RETRIES = {_cfg.RECALL_EMBEDDING_BACKFILL_MAX_RETRIES}")
    print(f"  HERMES_SUBSTRATE_RECALL (enable)      = {_cfg.HERMES_SUBSTRATE_RECALL_ENABLED}")


__all__ = ["print_summary", "print_recent", "print_sample", "print_config"]
