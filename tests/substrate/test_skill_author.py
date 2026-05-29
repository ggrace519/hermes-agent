"""Skill-author drafting logic — declinable, validated, slug-safe.

Pure-logic (no DB, mocked LLM client): the drafter must DECLINE non-skill-worthy
needs, reject malformed/garbage drafts before they can be staged, and normalise
slugs to valid skill names.
"""

from __future__ import annotations

import json

import pytest

from substrate.skill_proposals import author
from substrate.skill_proposals.author import DraftedSkill, _coerce, _slugify


def test_slugify_normalises_to_valid_name():
    assert _slugify("UniFi Site Query!") == "unifi-site-query"
    assert _slugify("  Multiple   Spaces  ") == "multiple-spaces"
    assert _slugify("already-good") == "already-good"


def test_coerce_declines_when_not_skill_worthy():
    assert _coerce({"skill_worthy": False}) is None
    assert _coerce({"skill_worthy": False, "slug": "x", "skill_md": "..."}) is None


def test_coerce_rejects_malformed_skill_md():
    # skill_worthy but the SKILL.md has no frontmatter → unusable, drop it.
    assert _coerce({
        "skill_worthy": True,
        "slug": "x-skill",
        "skill_md": "just prose, no frontmatter",
    }) is None
    # Frontmatter without name/description.
    assert _coerce({
        "skill_worthy": True,
        "slug": "x-skill",
        "skill_md": "---\nfoo: bar\n---\nbody",
    }) is None


def test_coerce_accepts_well_formed_draft():
    d = _coerce({
        "skill_worthy": True,
        "slug": "Good Skill",
        "title": "Good Skill",
        "rationale": "recurring",
        "skill_md": "---\nname: good-skill\ndescription: d\n---\n# body\nstep",
    })
    assert isinstance(d, DraftedSkill)
    assert d.slug == "good-skill"   # slugified


def test_coerce_rejects_invalid_slug():
    assert _coerce({
        "skill_worthy": True,
        "slug": "!!!",
        "skill_md": "---\nname: x\ndescription: d\n---\nbody",
    }) is None


class _FakeMsg:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})


class _FakeResp:
    def __init__(self, content):
        self.choices = [_FakeMsg(content)]


class _FakeClient:
    """Minimal stand-in for the async OpenAI client."""

    def __init__(self, content):
        self._content = content
        self.chat = type("C", (), {"completions": self})()

    async def create(self, **kw):
        return _FakeResp(self._content)


@pytest.mark.asyncio
async def test_draft_skill_returns_none_on_decline():
    client = _FakeClient(json.dumps({"skill_worthy": False}))
    assert await author.draft_skill("some need", client=client, model="m") is None


@pytest.mark.asyncio
async def test_draft_skill_parses_valid_output():
    payload = {
        "skill_worthy": True,
        "slug": "deploy-checklist",
        "title": "Deploy checklist",
        "rationale": "done often",
        "skill_md": "---\nname: deploy-checklist\ndescription: d\n---\n# steps\n1. go",
    }
    client = _FakeClient(json.dumps(payload))
    d = await author.draft_skill("recurring deploy", client=client, model="m")
    assert d is not None and d.slug == "deploy-checklist"


@pytest.mark.asyncio
async def test_draft_skill_handles_garbage_json():
    client = _FakeClient("not json at all")
    assert await author.draft_skill("need", client=client, model="m") is None


@pytest.mark.asyncio
async def test_draft_skill_empty_context_returns_none():
    assert await author.draft_skill("   ", client=_FakeClient("{}"), model="m") is None
