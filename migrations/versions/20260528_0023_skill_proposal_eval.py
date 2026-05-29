"""skill-proposal evaluator verdict — frontier-model judgment (Tier 1, Phase 2)

Phase 2 of the self-improvement forge
(``docs/plans/2026-05-28-substrate-self-improvement-forge.md``): a second,
*judgment-based* layer over the deterministic install-time scan
(``tools/skills_guard.py``). A frontier model reads each drafted SKILL.md and
judges it against a guardrail + design + intent rubric BEFORE the user sees it;
the verdict is stored here so the reviewer sees it and so ``gate`` mode can
auto-reject clear violations. Defense-in-depth — the human approval and the
deterministic scan remain the real gates; this is advisory by default.

Columns:
* ``eval_verdict``  — ``pass`` | ``flag`` | ``reject`` (NULL = not evaluated,
  e.g. no evaluator model configured / mode off).
* ``eval_reasons``  — JSONB array of short strings the judge gave.
* ``eval_model``    — which model produced the verdict (audit / calibration).
* ``evaluated_at``  — when (NULL when unevaluated).

Revision ID: 20260528_0023
Revises: 20260528_0022
Create Date: 2026-05-28
"""
from __future__ import annotations

from alembic import op


revision = "20260528_0023"
down_revision = "20260528_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE substrate_skill_proposals
            ADD COLUMN eval_verdict TEXT
                CHECK (eval_verdict IN ('pass', 'flag', 'reject')),
            ADD COLUMN eval_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN eval_model   TEXT,
            ADD COLUMN evaluated_at TIMESTAMPTZ
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE substrate_skill_proposals
            DROP COLUMN IF EXISTS evaluated_at,
            DROP COLUMN IF EXISTS eval_model,
            DROP COLUMN IF EXISTS eval_reasons,
            DROP COLUMN IF EXISTS eval_verdict
        """
    )
