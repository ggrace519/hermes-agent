"""``commit_slice`` latency microbenchmark — Phase A spec §12 acceptance #7.

Asserts that 1000 sequential commits on a warm pool finish with a p99
under the spec's 5 ms ceiling. ``warm pool`` means: the asyncpg
connection has already prepared the statement, the JSONB codec is
registered, and the stream metadata is cached in ``StreamRepo``.

This test is intentionally NOT marked ``integration`` because the spec
treats the p99 ceiling as an acceptance gate that must hold per-PR.
If the test starts flaking in CI on slower runners, raise the ceiling
(documenting the rationale) rather than skipping the assertion — we
want a hard ceiling on commit latency in this phase.
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from substrate import Substrate
from substrate.l0 import commit_slice
from substrate.storage import (
    DEFAULT_STRUCTURED_PROFILE,
    Family,
    Modality,
)


# Spec §12.7 ceiling. If CI is consistently noisy enough that 5 ms is
# unrealistic, bump *here* (with a comment recording the change), not by
# loosening the assertion at the call site.
_P99_CEILING_MS = 5.0

# 1000 commits per spec. Lower bound for the warmup phase that's
# excluded from the p99 calculation — the first commit on a fresh
# pooled connection pays the prepared-statement cost and would skew
# the distribution.
_WARMUP_COMMITS = 50
_MEASURED_COMMITS = 1000


@pytest_asyncio.fixture
async def warm_substrate(hermes_db_initialized):
    """Substrate with no sub-agent loops (we don't want Sentinel
    ticking and competing for connections during the benchmark)."""
    sub = await Substrate.boot(start_subagents=False)
    yield sub
    await sub.shutdown()


@pytest.mark.asyncio
async def test_commit_slice_p99_under_5ms(warm_substrate):
    """1000 commits, p99 < 5 ms. See module docstring for the rationale."""
    stream = await warm_substrate.streams.register(
        name="hermes.test.perf_warm_commit",
        family=Family.SELF_STATE,
        modality=Modality.STRUCTURED_EVENT,
        source="perftest",
        organ="pytest",
        decay_profile_id=DEFAULT_STRUCTURED_PROFILE,
    )

    # Warm-up: pay the prepared-statement cost + JSONB codec init on the
    # pooled connection(s) we'll exercise below. Excluded from the
    # distribution so the p99 reflects steady-state cost.
    now = datetime.now(timezone.utc)
    for _ in range(_WARMUP_COMMITS):
        await commit_slice(
            warm_substrate,
            stream.stream_id,
            {"warmup": True},
            event_time_world=now,
        )

    # Measured phase. ``time.perf_counter_ns`` for sub-microsecond
    # resolution; the bench is ~1 second total so resolution matters.
    latencies_ns: list[int] = []
    for i in range(_MEASURED_COMMITS):
        t0 = time.perf_counter_ns()
        await commit_slice(
            warm_substrate,
            stream.stream_id,
            {"i": i},
            event_time_world=now,
        )
        latencies_ns.append(time.perf_counter_ns() - t0)

    # Stats — milliseconds for the assertion.
    latencies_ms = [ns / 1_000_000 for ns in latencies_ns]
    p50 = statistics.median(latencies_ms)
    # statistics.quantiles with n=100 gives 99 cut points; index 98 is
    # the p99 boundary (values <= it cover 99% of the distribution).
    p99 = statistics.quantiles(latencies_ms, n=100, method="inclusive")[98]
    p_max = max(latencies_ms)

    # Diagnostic output — pytest prints this on failure AND on -s.
    print(
        f"\ncommit_slice latency (n={_MEASURED_COMMITS}): "
        f"p50={p50:.2f}ms  p99={p99:.2f}ms  max={p_max:.2f}ms"
    )

    assert p99 < _P99_CEILING_MS, (
        f"commit_slice p99 = {p99:.2f}ms exceeds the spec §12.7 ceiling "
        f"of {_P99_CEILING_MS}ms (p50={p50:.2f}ms, max={p_max:.2f}ms). "
        "Profile the INSERT path before merging."
    )
