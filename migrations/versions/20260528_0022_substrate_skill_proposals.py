"""substrate_skill_proposals — staged, human-gated self-authored skills

Phase 1 of the self-improvement plan
(``docs/plans/2026-05-28-substrate-self-improvement-forge.md``, Tier 1):
the substrate's SkillScout sub-agent discovers recurring/important needs in
its own upper-layer memory (L3 patterns / L4 observations) and drafts a new
skill for each. Drafts are NOT installed — they land here as ``pending``
proposals and the user reviews/approves them in chat. The pending row IS the
Tier-1 quarantine: a draft is inert until a human approves it (which then
promotes it via ``skill_manage action=create``).

This table stores the draft + the provenance (which L3/L4 rows triggered it,
the salience that crossed the bar) so a reviewer can judge the grounding, and
so the SkillScout can dedup against already-proposed/decided needs and not
re-pester. ``slug`` is unique: one proposal per prospective skill name, ever —
a rejected slug is therefore never re-proposed (intentional soft dedup).

Revision ID: 20260528_0022
Revises: 20260527_0021
Create Date: 2026-05-28
"""
from __future__ import annotations

from alembic import op


revision = "20260528_0022"
down_revision = "20260527_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE substrate_skill_proposals (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            slug           TEXT NOT NULL UNIQUE,
            title          TEXT NOT NULL,
            draft_content  TEXT NOT NULL,
            rationale      TEXT NOT NULL DEFAULT '',
            status         TEXT NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'approved', 'rejected')),
            source_l3_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
            source_l4_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
            salience       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            decided_at     TIMESTAMPTZ,
            decided_by     TEXT
        )
        """
    )
    # list_proposals(status=...) — pending-first review is the hot path.
    op.execute(
        "CREATE INDEX idx_skill_proposals_status "
        "ON substrate_skill_proposals (status, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_skill_proposals_status")
    op.execute("DROP TABLE IF EXISTS substrate_skill_proposals")
