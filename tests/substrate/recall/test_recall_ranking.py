"""rank_candidates — Phase C Task 5 / spec §9.3.

Pure-function tests against the ranker. No DB. No async. Mock
embeddings + hand-crafted RecallCandidates.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from substrate.recall.projection import (
    DEFAULT_RECENCY_HALF_LIFE_HOURS,
    RecallCandidate,
    rank_candidates,
)
from substrate.storage.types import Address


def _addr() -> Address:
    return Address(uuid4(), datetime.now(timezone.utc), datetime.now(timezone.utc))


def _candidate(
    *,
    payload: str = "",
    salience: float = 0.5,
    age: timedelta = timedelta(0),
    embedding: list[float] | None = None,
    t_now: datetime | None = None,
) -> RecallCandidate:
    now = t_now or datetime.now(timezone.utc)
    return RecallCandidate(
        address=_addr(),
        stream_name="test.stream",
        payload=payload,
        event_time_world=now - age,
        salience_score=salience,
        trust_score=None,
        metadata={},
        embedding=embedding,
    )


def test_rank_candidates_salience_dominates_when_recency_tied():
    """Higher salience wins on equal recency (and no semantic signal)."""
    t = datetime.now(timezone.utc)
    lo = _candidate(payload="ignore", salience=0.2, t_now=t)
    hi = _candidate(payload="ignore", salience=0.8, t_now=t)
    out = rank_candidates([lo, hi], query="unrelated", t_now=t)
    assert out[0] is hi
    assert out[1] is lo


def test_rank_candidates_semantic_similarity_boost():
    """With matching embeddings, cosine ~1 boosts a candidate above an
    equally-recent, equally-salient one whose embedding is orthogonal."""
    t = datetime.now(timezone.utc)
    # Unit vectors.
    e_match = [1.0] + [0.0] * 1535
    e_ortho = [0.0, 1.0] + [0.0] * 1534
    match = _candidate(salience=0.5, embedding=e_match, t_now=t)
    other = _candidate(salience=0.5, embedding=e_ortho, t_now=t)
    out = rank_candidates([other, match], query="x", query_embedding=e_match, t_now=t)
    assert out[0] is match


def test_rank_candidates_keyword_fallback_when_no_embedding():
    """``query_embedding=None`` forces keyword Jaccard for every candidate."""
    t = datetime.now(timezone.utc)
    a = _candidate(payload="the user asked about coffee", salience=0.5, t_now=t)
    b = _candidate(payload="entirely unrelated text", salience=0.5, t_now=t)
    out = rank_candidates(
        [b, a], query="coffee preferences", query_embedding=None, t_now=t
    )
    assert out[0] is a


def test_rank_candidates_per_candidate_fallback():
    """Mixed batch: half embedded, half not. The embedded ones use cosine,
    the unembedded ones use Jaccard, in the same call.

    With the default weights, the strong semantic match (cosine ~1)
    outscores everything else, and the strong keyword match (jaccard
    ~1) outscores the keyword-mismatch. The ordering exercises both
    paths in one call.
    """
    t = datetime.now(timezone.utc)
    qvec = [1.0] + [0.0] * 1535
    # Embedded match (semantic path).
    em_match = _candidate(salience=0.3, embedding=qvec, t_now=t)
    # Unembedded but keyword-matches the query exactly (jaccard = 1.0).
    kw_match = _candidate(payload="coffee", salience=0.3, t_now=t)
    # Unembedded and unrelated.
    kw_other = _candidate(payload="dog walking", salience=0.3, t_now=t)
    out = rank_candidates(
        [kw_other, em_match, kw_match],
        query="coffee",
        query_embedding=qvec,
        t_now=t,
    )
    # The semantic match is the highest scorer (cosine 1 + salience +
    # recency); the keyword match is next; the unrelated keyword
    # candidate is last.
    assert out[0] is em_match
    assert out[1] is kw_match
    assert out[2] is kw_other


def test_rank_candidates_recency_decay_half_life_12h():
    """At age = half-life, recency term = 0.5; at 2x half-life, 0.25; at
    0, 1.0. With salience zero and no similarity, the score IS the
    recency term × weight."""
    t = datetime.now(timezone.utc)
    # We construct candidates with zero salience and unrelated payload so
    # the only contributor to score is the recency term.
    c_now = _candidate(payload="x", salience=0.0, age=timedelta(0), t_now=t)
    c_half = _candidate(
        payload="x", salience=0.0,
        age=timedelta(hours=DEFAULT_RECENCY_HALF_LIFE_HOURS), t_now=t
    )
    c_dbl = _candidate(
        payload="x", salience=0.0,
        age=timedelta(hours=2 * DEFAULT_RECENCY_HALF_LIFE_HOURS), t_now=t
    )
    out = rank_candidates([c_dbl, c_half, c_now], query="y", t_now=t)
    # Order should be: most-recent first.
    assert out[0] is c_now
    assert out[1] is c_half
    assert out[2] is c_dbl


def test_rank_candidates_tiebreak_more_recent():
    """Two candidates with identical composite scores: more recent wins."""
    t = datetime.now(timezone.utc)
    older = _candidate(payload="x", salience=0.5, age=timedelta(minutes=10), t_now=t)
    newer = _candidate(payload="x", salience=0.5, age=timedelta(minutes=5), t_now=t)
    # Same payload, same salience — composite tied except for the recency
    # term, which favours the newer one. Order: newer, older.
    out = rank_candidates([older, newer], query="z", t_now=t)
    assert out[0] is newer
    assert out[1] is older


def test_rank_candidates_handles_empty_input():
    """Empty input → empty output, no exceptions."""
    assert rank_candidates([], query="anything", t_now=datetime.now(timezone.utc)) == []


def test_rank_candidates_handles_payload_dict():
    """Structured-event payload (dict) is tokenised via its text field
    when present; falls back to JSON soup otherwise."""
    t = datetime.now(timezone.utc)
    a = _candidate(payload={"text": "coffee beans"}, salience=0.0, t_now=t)
    b = _candidate(payload={"foo": "dog walking"}, salience=0.0, t_now=t)
    out = rank_candidates([b, a], query="coffee", t_now=t)
    assert out[0] is a
