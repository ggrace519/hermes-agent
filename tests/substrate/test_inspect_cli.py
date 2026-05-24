"""Tests for ``substrate.cli.inspect`` — the debug subcommand.

Exercises the printer functions directly against the PG fixture so the
tests don't have to spin up the full ``hermes`` argparse tree. The
``register_subparser`` wiring is smoke-tested separately (it's a thin
shell of argparse calls).
"""

from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from substrate import Substrate
from substrate.cli import inspect as inspect_mod
from substrate.events.hermes_hooks import on_user_message_async


@pytest_asyncio.fixture
async def booted_substrate(hermes_db_initialized):
    """Boot substrate with sub-agents off — the inspect tests are
    deterministic about queue depth, so we don't want Sentinel passing
    slices mid-test.
    """
    sub = await Substrate.boot(start_subagents=False)
    yield sub
    await sub.shutdown()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# register_subparser — smoke-test the argparse wiring.
# ---------------------------------------------------------------------------


def test_register_subparser_adds_inspect_tree():
    """The ``substrate`` parser plus ``inspect`` subparsers must be
    addable to a fresh top-level parser without errors."""
    parser = argparse.ArgumentParser(prog="hermes")
    sub = parser.add_subparsers(dest="command")
    inspect_mod.register_subparser(sub)

    # Successful parse of `substrate inspect streams`.
    ns = parser.parse_args(["substrate", "inspect", "streams"])
    assert ns.command == "substrate"
    assert callable(ns.func)


def test_register_subparser_slices_requires_stream_arg():
    parser = argparse.ArgumentParser(prog="hermes")
    sub = parser.add_subparsers(dest="command")
    inspect_mod.register_subparser(sub)

    with pytest.raises(SystemExit):
        parser.parse_args(["substrate", "inspect", "slices"])  # --stream missing


# ---------------------------------------------------------------------------
# Default summary printer — covers the §10.2 output shape.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_print_summary_after_boot_contains_expected_sections(
    booted_substrate,
):
    """The default summary lists streams + slice counts + pending +
    sub-agents headings (spec §10.2)."""
    import hermes_db

    # Emit one user-message slice so the summary has non-zero counts.
    await on_user_message_async("sess-cli-1", "cli", "hello", _now_utc())

    buf = io.StringIO()
    async with hermes_db.connection() as conn:
        with redirect_stdout(buf):
            await inspect_mod._print_summary(conn)
    out = buf.getvalue()

    assert "Substrate state @" in out
    assert "Streams:" in out
    assert "15 registered" in out  # the §9 set plus self_state
    assert "Slices:" in out
    assert "pending" in out
    assert "passed" in out
    assert "Pending queue:" in out
    assert "Sub-agents (intensity):" in out
    assert "sentinel" in out


@pytest.mark.asyncio
async def test_print_streams_lists_autoregistered(booted_substrate):
    import hermes_db

    buf = io.StringIO()
    async with hermes_db.connection() as conn:
        with redirect_stdout(buf):
            await inspect_mod._print_streams(conn)
    out = buf.getvalue()

    # Header + every §9 stream name appears in the listing.
    assert "name" in out and "modality" in out
    for name in (
        "hermes.world.user_message.cli",
        "hermes.world.user_message.telegram",
        "hermes.self_action.assistant_response",
        "hermes.self_action.tool_call",
        "hermes.self_state.tool_result",
        "hermes.self_state.session_lifecycle",
        "hermes.self_state.cron_dispatch",
        "substrate.self_state",
    ):
        assert name in out, f"stream {name} missing from streams listing"


@pytest.mark.asyncio
async def test_print_slices_for_named_stream(booted_substrate):
    """``inspect slices --stream NAME`` returns the most-recent slices for
    that stream and prints their addresses + payload preview."""
    import hermes_db

    for i in range(3):
        await on_user_message_async(
            f"sess-{i}", "cli", f"msg {i}", _now_utc()
        )

    buf = io.StringIO()
    async with hermes_db.connection() as conn:
        with redirect_stdout(buf):
            await inspect_mod._print_slices(
                conn, stream_name="hermes.world.user_message.cli", limit=10
            )
    out = buf.getvalue()
    assert "Most-recent 3 slices" in out
    # Payload preview contains the wrapped TEXT shape.
    assert "text" in out


@pytest.mark.asyncio
async def test_print_slices_unknown_stream(booted_substrate):
    import hermes_db

    buf = io.StringIO()
    async with hermes_db.connection() as conn:
        with redirect_stdout(buf):
            await inspect_mod._print_slices(
                conn, stream_name="hermes.does.not.exist", limit=5
            )
    out = buf.getvalue()
    assert "no slices" in out


@pytest.mark.asyncio
async def test_print_pending_reports_zero_initially(booted_substrate):
    """With sub-agents off and no commits, pending queue is empty."""
    import hermes_db

    buf = io.StringIO()
    async with hermes_db.connection() as conn:
        with redirect_stdout(buf):
            await inspect_mod._print_pending(conn)
    out = buf.getvalue()
    assert "depth: 0" in out
    assert "no pending slices" in out


@pytest.mark.asyncio
async def test_print_pending_reports_age_after_emit(booted_substrate):
    """A fresh commit shows up with depth=1 and an age in seconds."""
    import hermes_db

    await on_user_message_async("sess-p", "cli", "queued", _now_utc())
    buf = io.StringIO()
    async with hermes_db.connection() as conn:
        with redirect_stdout(buf):
            await inspect_mod._print_pending(conn)
    out = buf.getvalue()
    assert "depth: 1" in out
    assert "oldest age:" in out
    assert "s" in out  # seconds suffix


@pytest.mark.asyncio
async def test_print_profiles_lists_4_seeded(booted_substrate):
    import hermes_db

    buf = io.StringIO()
    async with hermes_db.connection() as conn:
        with redirect_stdout(buf):
            await inspect_mod._print_profiles(conn)
    out = buf.getvalue()
    for profile in ("default-text", "default-structured", "default-binary", "default-signal"):
        assert profile in out
