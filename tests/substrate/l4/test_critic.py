"""L4 store + deterministic Critic + inspect surface."""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout

import pytest
import pytest_asyncio

from substrate import Substrate
from substrate.config import SubstrateConfig
from substrate.l4 import store as l4
from substrate.agents.critic import Critic
from substrate.cli import inspect as inspect_mod


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_and_latest_coherence(hermes_db_initialized):
    await l4.record_observation("coherence", "substrate", "coherence 0.90", score=0.9)
    await l4.record_observation("coherence", "substrate", "coherence 0.70", score=0.7)
    latest = await l4.latest_coherence()
    assert latest is not None and latest.score == pytest.approx(0.7)  # most recent


@pytest.mark.asyncio
async def test_list_observations_filters(hermes_db_initialized):
    await l4.record_observation("calibration", "parser", "ok 90%", score=0.9)
    await l4.record_observation("calibration", "consolidation", "backlog 10%", score=0.9)
    parser_obs = await l4.list_observations(subject="parser")
    assert parser_obs and all(o.subject == "parser" for o in parser_obs)


# ---------------------------------------------------------------------------
# coherence math (pure)
# ---------------------------------------------------------------------------


def test_coherence_penalises_backlog_and_alarms():
    base = Critic._coherence({"backlog_ratio": 0.0, "parser_reliability": 1.0, "alarms_1h": 0})
    assert base == pytest.approx(1.0)
    backlogged = Critic._coherence(
        {"backlog_ratio": 1.0, "parser_reliability": 1.0, "alarms_1h": 0}
    )
    assert backlogged == pytest.approx(0.6)  # 1.0 - 0.4
    alarmed = Critic._coherence(
        {"backlog_ratio": 0.0, "parser_reliability": 1.0, "alarms_1h": 3}
    )
    assert alarmed == pytest.approx(0.8)  # 1.0 - 0.2
    unreliable = Critic._coherence(
        {"backlog_ratio": 0.0, "parser_reliability": 0.4, "alarms_1h": 0}
    )
    assert unreliable < 1.0


# ---------------------------------------------------------------------------
# Critic agent
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def booted(hermes_db_initialized):
    sub = await Substrate.boot(
        config=SubstrateConfig(auto_migrate=False, start_subagents=False),
        start_subagents=False,
    )
    try:
        yield sub
    finally:
        await sub.shutdown()


@pytest.mark.asyncio
async def test_critic_disabled_is_noop(booted, monkeypatch):
    monkeypatch.setenv("HERMES_SUBSTRATE_CRITIC", "0")
    await Critic(booted).tick()
    assert await l4.list_observations() == []


@pytest.mark.asyncio
async def test_critic_records_coherence_and_calibration(booted, monkeypatch):
    import hermes_db

    monkeypatch.setenv("HERMES_SUBSTRATE_CRITIC", "1")
    # Seed a parser_log row so reliability has data.
    async with hermes_db.connection() as conn:
        await conn.execute(
            "INSERT INTO substrate_parser_log (batch_size, latency_ms, model, outcome) "
            "VALUES (10, 100, 'm', 'ok')"
        )
    await Critic(booted).tick()

    coh = await l4.latest_coherence()
    assert coh is not None and 0.0 <= coh.score <= 1.0
    cal = await l4.list_observations(kind="calibration")
    subjects = {o.subject for o in cal}
    assert "parser" in subjects and "consolidation" in subjects


@pytest.mark.asyncio
async def test_critic_rate_limited(booted, monkeypatch):
    monkeypatch.setenv("HERMES_SUBSTRATE_CRITIC", "1")
    monkeypatch.setenv("CRITIC_INTERVAL_S", "3600")
    c = Critic(booted)
    await c.tick()
    n1 = len(await l4.list_observations(limit=100))
    await c.tick()  # within the interval → no new observations
    n2 = len(await l4.list_observations(limit=100))
    assert n1 == n2


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


def test_register_subparser_l4():
    parser = argparse.ArgumentParser(prog="hermes")
    sub = parser.add_subparsers(dest="command")
    inspect_mod.register_subparser(sub)
    assert callable(parser.parse_args(["substrate", "l4", "observations"]).func)


@pytest.mark.asyncio
async def test_print_l4(hermes_db_initialized):
    import hermes_db

    await l4.record_observation("coherence", "substrate", "coherence 0.88", score=0.88)
    buf = io.StringIO()
    async with hermes_db.connection() as conn:
        with redirect_stdout(buf):
            await inspect_mod._print_l4(conn)
    out = buf.getvalue()
    assert "Coherence (latest): 0.88" in out
