"""Skill-proposal dataclass — a staged, human-gated self-authored skill.

A proposal is a drafted ``SKILL.md`` that the SkillScout produced from a
recurring/important need in the upper-layer memory, plus the provenance that
justifies it. It is inert until a human approves it (then it's promoted via
``skill_manage action=create``). See
``docs/plans/2026-05-28-substrate-self-improvement-forge.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

PROPOSAL_STATUSES = frozenset({"pending", "approved", "rejected"})


@dataclass(frozen=True)
class SkillProposal:
    id: UUID
    slug: str
    title: str
    draft_content: str       # the full proposed SKILL.md (frontmatter + body)
    rationale: str           # why the SkillScout thinks this is skill-worthy
    status: str              # pending | approved | rejected
    source_l3_ids: list[str]  # L3 pattern ids that triggered this (provenance)
    source_l4_ids: list[str]  # L4 observation ids that triggered this
    salience: float          # the triggering salience that crossed the bar
    created_at: datetime
    decided_at: Optional[datetime]
    decided_by: Optional[str]


__all__ = ["PROPOSAL_STATUSES", "SkillProposal"]
