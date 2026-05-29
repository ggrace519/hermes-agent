"""Skill proposals — staged, human-gated self-authored skills (Tier 1).

The SkillScout sub-agent drafts skills from recurring/important needs in the
upper-layer memory and stages them here as ``pending`` proposals; the user
reviews and approves them in chat. See
``docs/plans/2026-05-28-substrate-self-improvement-forge.md``.
"""

from substrate.skill_proposals.schema import PROPOSAL_STATUSES, SkillProposal

__all__ = ["PROPOSAL_STATUSES", "SkillProposal"]
