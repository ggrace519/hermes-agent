"""Proactive notify helper — graceful, best-effort, never raises.

The substrate worker uses this to message the user (e.g. "I drafted a skill").
It must degrade quietly: empty message → error, no home channel → error string
(not an exception), and a per-channel send failure must not bubble out.
"""

from __future__ import annotations

import pytest

from substrate import notify


@pytest.mark.asyncio
async def test_empty_message_is_rejected():
    assert await notify.notify_user("") == ["empty message"]
    assert await notify.notify_user("   ") == ["empty message"]


@pytest.mark.asyncio
async def test_no_home_channel_returns_error_not_raise(monkeypatch):
    monkeypatch.setattr(notify, "_home_targets", lambda: iter(()))
    # discover_plugins / _send_to_platform are never reached when there are no
    # targets; the call must return an error list rather than raise.
    errs = await notify.notify_user("hello")
    assert errs == ["no home channel configured"]


@pytest.mark.asyncio
async def test_resolution_failure_returns_error_not_raise(monkeypatch):
    def _boom():
        raise RuntimeError("gateway stack missing")

    monkeypatch.setattr(notify, "_home_targets", _boom)
    errs = await notify.notify_user("hello")
    assert errs and "no delivery target" in errs[0]


@pytest.mark.asyncio
async def test_delivery_to_any_channel_clears_errors(monkeypatch):
    class _P:
        value = "telegram"

    monkeypatch.setattr(
        notify, "_home_targets", lambda: iter([(_P(), object(), "chat1", None)])
    )

    async def _ok(platform, pconfig, chat_id, text, thread_id=None):
        return {"success": True}

    monkeypatch.setattr("tools.send_message_tool._send_to_platform", _ok)
    assert await notify.notify_user("hi") == []


@pytest.mark.asyncio
async def test_send_exception_is_caught(monkeypatch):
    class _P:
        value = "discord"

    monkeypatch.setattr(
        notify, "_home_targets", lambda: iter([(_P(), object(), "c", None)])
    )

    async def _raise(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("tools.send_message_tool._send_to_platform", _raise)
    errs = await notify.notify_user("hi")
    assert errs and "discord" in errs[0]
