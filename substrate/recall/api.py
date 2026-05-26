"""Public recall API — Phase C Tasks 7 + 10 / spec §4.

``recall(...)`` is the async entry point that the
:class:`SubstrateMemoryProvider` calls. It orchestrates the pipeline:

  1. embed_query (optional, timeout-bounded)
  2. recall_window SQL (timeout-bounded; the only step that can timeout
     the whole call — embedding + ranking are bounded by their own
     budgets but the SQL is the load-bearing latency contributor)
  3. rank_candidates (pure-function)
  4. compose_projection (pure-function, token-budgeted)
  5. reinforce_hits (fire-and-forget per-slice via Phase B reinforce_slice)
  6. log_recall (enqueue to RecallLogWriter, non-blocking)

Failures NEVER reach the caller — the function always returns a
:class:`RecallProjection`, possibly empty with ``empty_reason`` set.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from substrate import config as _cfg
from substrate.recall.composer import compose_projection
from substrate.recall.embeddings import embed_query
from substrate.recall.log import RecallLogRow
from substrate.recall.projection import (
    RecallCandidate,
    RecallProjection,
    rank_candidates,
)

if TYPE_CHECKING:  # pragma: no cover
    from substrate.facade import Substrate


_log = logging.getLogger("substrate.recall.api")


# ---------------------------------------------------------------------------
# In-process reinforcement rate-limit (spec §5.4).
#
# Bounded by an LRU of recent timestamps per slice_id. ``_REINFORCE_LRU``
# is process-wide — correct for single-process Hermes (the gateway loops
# 128 AIAgents inside one process). Multi-process scale-out would need
# this to move to PG; that's Phase G.
# ---------------------------------------------------------------------------


_REINFORCE_LRU: dict[UUID, list[float]] = {}
_REINFORCE_LRU_MAX_SIZE = 1024


def _evict_lru_if_full() -> None:
    """Drop the oldest entry when the LRU dict grows past the cap."""
    if len(_REINFORCE_LRU) <= _REINFORCE_LRU_MAX_SIZE:
        return
    # dict iteration order in Python 3.7+ is insertion order — first
    # key is the oldest. Pop it.
    oldest = next(iter(_REINFORCE_LRU))
    _REINFORCE_LRU.pop(oldest, None)


def _reinforce_allowed(slice_id: UUID, now: float) -> bool:
    """Check + record a reinforcement under the per-slice rate cap.

    Returns True if the caller may proceed with the reinforcement;
    False if the slice has already received the maximum bumps in the
    last 60 seconds. Has a side effect — when True, records the new
    timestamp.
    """
    history = _REINFORCE_LRU.get(slice_id, [])
    # Drop timestamps older than 60s.
    history = [t for t in history if t > now - 60.0]
    if len(history) >= _cfg.RECALL_REINFORCE_RATE_LIMIT_PER_MIN:
        _REINFORCE_LRU[slice_id] = history
        return False
    history.append(now)
    _REINFORCE_LRU[slice_id] = history
    _evict_lru_if_full()
    return True


def _summarise_embedding_path(
    query_embedding: Optional[list[float]],
    composed: list[RecallCandidate],
) -> str:
    """Tag the recall call with the embedding-path it used (spec §5.2).

    Returns one of: 'semantic' (all composed had embeddings + query
    had embedding), 'keyword' (no embeddings used at all), 'mixed'
    (some composed had embeddings, others didn't), or 'empty' (no
    composed candidates)."""
    if not composed:
        return "empty"
    if query_embedding is None:
        return "keyword"
    embedded = sum(1 for c in composed if c.embedding is not None)
    if embedded == len(composed):
        return "semantic"
    if embedded == 0:
        return "keyword"
    return "mixed"


async def _reinforce_hits(
    substrate: "Substrate",
    composed: list[RecallCandidate],
) -> int:
    """Fire reinforcement for each composed slice, subject to the
    per-slice rate cap. Failures are logged + swallowed (the recall
    pipeline never raises to its caller).

    Returns the number of reinforcements actually applied (useful for
    observability)."""
    from substrate.l0.api import reinforce_slice

    now = time.time()
    applied = 0
    for c in composed:
        slice_id = c.slice_id
        if not _reinforce_allowed(slice_id, now):
            continue
        try:
            await reinforce_slice(substrate, slice_id)
            applied += 1
        except Exception as exc:
            _log.warning(
                "reinforce after recall failed for slice %s: %s",
                slice_id,
                exc,
            )
    return applied


# ---------------------------------------------------------------------------
# Public surface — recall + sync facade.
# ---------------------------------------------------------------------------


async def recall(
    substrate: "Substrate",
    query: str,
    *,
    session_id: Optional[str] = None,
    t_now: Optional[datetime] = None,
    token_budget: Optional[int] = None,
    time_window: Optional[timedelta] = None,
    stream_filter: Optional[list[str]] = None,
    min_salience: Optional[float] = None,
    candidate_limit: Optional[int] = None,
    recall_timeout_ms: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> RecallProjection:
    """Compose a salience-weighted, time-windowed, token-budgeted text
    projection of L0 slices relevant to ``query``.

    Defaults are sourced from ``substrate.config`` (env-tunable per
    spec §5.6); pass explicit kwargs to override per-call.

    Always returns a ``RecallProjection``. On any internal failure the
    returned projection has ``text=""`` and ``empty_reason`` set
    explaining why (no_candidates / budget_zero / all_truncated /
    timeout / db_error). The caller (SubstrateMemoryProvider) never
    needs to try/except — substrate failures never reach Hermes's call
    site (mirrors the Phase A hook discipline).
    """
    t_now = t_now or datetime.now(timezone.utc)
    token_budget = (
        token_budget if token_budget is not None else _cfg.RECALL_TOKEN_BUDGET
    )
    time_window = (
        time_window if time_window is not None
        else timedelta(hours=_cfg.RECALL_TIME_WINDOW_HOURS)
    )
    stream_filter = stream_filter or list(_cfg.DEFAULT_RECALL_STREAMS)
    min_salience = (
        min_salience if min_salience is not None else _cfg.RECALL_MIN_SALIENCE
    )
    candidate_limit = (
        candidate_limit if candidate_limit is not None
        else _cfg.RECALL_CANDIDATE_LIMIT
    )
    recall_timeout_ms = (
        recall_timeout_ms if recall_timeout_ms is not None
        else _cfg.RECALL_TIMEOUT_MS
    )

    t_start = time.monotonic()

    # 1+2. Embed the query and fetch candidates — both wrapped in the
    # recall_timeout_ms budget. We run them sequentially because the
    # SQL needs no embedding input; the embedding is for ranking after
    # the SQL returns.
    try:
        candidates = await asyncio.wait_for(
            _fetch_candidates(
                substrate,
                t_now=t_now,
                time_window=time_window,
                stream_names=stream_filter,
                min_salience=min_salience,
                limit=candidate_limit,
            ),
            timeout=recall_timeout_ms / 1000.0,
        )
    except asyncio.TimeoutError:
        duration_ms = int((time.monotonic() - t_start) * 1000)
        proj = RecallProjection(
            text="", tokens_used=0, composed=[], candidates_seen=0,
            duration_ms=duration_ms, timed_out=True, empty_reason="timeout",
        )
        _safe_enqueue_log(
            substrate, t_now, session_id, query, proj, metadata,
            error_text="recall window timed out",
        )
        return proj
    except Exception as exc:
        duration_ms = int((time.monotonic() - t_start) * 1000)
        _log.warning("recall db error: %s", exc)
        proj = RecallProjection(
            text="", tokens_used=0, composed=[], candidates_seen=0,
            duration_ms=duration_ms, timed_out=False, empty_reason="db_error",
        )
        _safe_enqueue_log(
            substrate, t_now, session_id, query, proj, metadata,
            error_text=str(exc),
        )
        return proj

    # 1b. Embed the query (best-effort; None on failure → keyword path
    # forced for all candidates). Bounded by its own timeout from the
    # embeddings module. ``RECALL_EMBEDDING_MODEL`` is the override knob
    # (None by default) — when unset, ``embed_query`` reads
    # ``auxiliary.embedding.model`` from config. Forcing a model name
    # here would override the operator's provider choice and 404 on
    # non-OpenAI endpoints. See substrate/config.py.
    eq_kwargs = {"timeout_ms": _cfg.RECALL_EMBEDDING_TIMEOUT_MS}
    if _cfg.RECALL_EMBEDDING_MODEL is not None:
        eq_kwargs["model"] = _cfg.RECALL_EMBEDDING_MODEL
    try:
        query_embedding = await embed_query(query, **eq_kwargs)
    except Exception as exc:
        _log.debug("query embedding failed: %s", exc)
        query_embedding = None

    # 3. Rank.
    ranked = rank_candidates(
        candidates,
        query,
        query_embedding,
        t_now=t_now,
        similarity_weight=_cfg.RECALL_SIMILARITY_WEIGHT,
        keyword_overlap_weight=_cfg.RECALL_KEYWORD_WEIGHT,
        salience_weight=_cfg.RECALL_SALIENCE_WEIGHT,
        recency_weight=_cfg.RECALL_RECENCY_WEIGHT,
        recency_half_life_hours=_cfg.RECALL_RECENCY_HALF_LIFE_HOURS,
    )

    # 4. Compose.
    text, composed, tokens = compose_projection(ranked, token_budget=token_budget)

    # 5. Reinforce hits (no await on failure — fire-and-forget).
    # Note: we await here for testability; the actual work is per-slice
    # rate-limited so this is short. A future async-only refactor can
    # promote to asyncio.create_task.
    try:
        await _reinforce_hits(substrate, composed)
    except Exception as exc:
        _log.warning("reinforce hits batch failed: %s", exc)

    duration_ms = int((time.monotonic() - t_start) * 1000)

    # Derive empty_reason for observability.
    if text:
        empty_reason = None
    elif token_budget == 0:
        empty_reason = "budget_zero"
    elif not candidates:
        empty_reason = "no_candidates"
    else:
        empty_reason = "all_truncated"

    proj = RecallProjection(
        text=text,
        tokens_used=tokens,
        composed=composed,
        candidates_seen=len(candidates),
        duration_ms=duration_ms,
        timed_out=False,
        empty_reason=empty_reason,
    )

    # 6. Log. The metadata blob captures embedding-path tag for the
    # operator-validation window (spec §5.2).
    embedding_path = _summarise_embedding_path(query_embedding, composed)
    extra_meta = dict(metadata or {})
    extra_meta.update(
        empty_reason=empty_reason,
        embedding_path=embedding_path,
    )
    _safe_enqueue_log(
        substrate, t_now, session_id, query, proj, extra_meta, error_text=None,
    )
    return proj


async def _fetch_candidates(
    substrate: "Substrate",
    *,
    t_now: datetime,
    time_window: timedelta,
    stream_names: list[str],
    min_salience: float,
    limit: int,
) -> list[RecallCandidate]:
    """Acquire a connection and run the recall_window query.

    Separated from ``recall()`` so the timeout wrap is clean — the
    connection-acquisition + SQL execution are both inside the
    asyncio.wait_for boundary.
    """
    import hermes_db

    async with hermes_db.connection() as conn:
        return await substrate.slices.recall_window(
            conn,
            t_now=t_now,
            time_window=time_window,
            stream_names=stream_names,
            min_salience=min_salience,
            limit=limit,
        )


def _safe_enqueue_log(
    substrate: "Substrate",
    t_now: datetime,
    session_id: Optional[str],
    query: str,
    proj: RecallProjection,
    metadata: Optional[dict],
    *,
    error_text: Optional[str],
) -> None:
    """Enqueue a recall_log row, swallowing any error (the log writer
    may not be attached, e.g. in unit tests that bypass Substrate.boot)."""
    writer = getattr(substrate, "recall_log", None)
    if writer is None:
        return
    try:
        writer.enqueue(
            RecallLogRow(
                requested_at=t_now,
                session_id=session_id,
                query_excerpt=(query or "")[:200],
                candidates_count=proj.candidates_seen,
                composed_count=len(proj.composed),
                tokens_used=proj.tokens_used,
                duration_ms=proj.duration_ms,
                timed_out=proj.timed_out,
                error_text=error_text,
                metadata=dict(metadata or {}),
            )
        )
    except Exception as exc:
        _log.debug("recall log enqueue failed: %s", exc)


def recall_sync(
    substrate: "Substrate",
    query: str,
    *,
    session_id: Optional[str] = None,
    t_now: Optional[datetime] = None,
    token_budget: Optional[int] = None,
    time_window: Optional[timedelta] = None,
    stream_filter: Optional[list[str]] = None,
    min_salience: Optional[float] = None,
    candidate_limit: Optional[int] = None,
    recall_timeout_ms: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> RecallProjection:
    """Sync facade — bridges to the async ``recall`` via
    :func:`hermes_db.run_sync`. Must NOT be called from inside a
    running event loop (the underlying ``run_sync`` raises).
    """
    import hermes_db

    return hermes_db.run_sync(
        recall(
            substrate,
            query,
            session_id=session_id,
            t_now=t_now,
            token_budget=token_budget,
            time_window=time_window,
            stream_filter=stream_filter,
            min_salience=min_salience,
            candidate_limit=candidate_limit,
            recall_timeout_ms=recall_timeout_ms,
            metadata=metadata,
        )
    )


__all__ = ["recall", "recall_sync"]
