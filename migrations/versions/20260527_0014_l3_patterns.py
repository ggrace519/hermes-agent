"""substrate L3 — patterns / abstractions (Phase E2)

The Pattern-finder sub-agent reads L1 entities + relationships (and, later,
L2 associations) and distils higher-order observations — generalizations,
recurring themes, recurring structures — into ``l3_patterns``. Each pattern
cites the L1 entities it generalizes from.

Distinct from L1 (explicit facts the Parser extracted) and L2 (discovered
pairwise associations): L3 is *abstraction across many extractions*
(MVS §3.2 "L3: patterns and abstractions"). Patterns dedup on
``(kind, statement)``; re-finding bumps salience + last_seen.

Per the Phase E2 spec (2026-05-27-phase-e2-l3-patternfinder.md).

Revision ID: 20260527_0014
Revises: 20260527_0013
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op


revision = "20260527_0014"
down_revision = "20260527_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE l3_patterns (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            kind            TEXT NOT NULL
                CHECK (kind IN ('generalization','theme','recurring_structure','other')),
            statement       TEXT NOT NULL,
            cites           JSONB NOT NULL DEFAULT '[]'::jsonb,
            salience_score  DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            confidence      DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
            UNIQUE (kind, statement)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_l3_patterns_statement_trgm "
        "ON l3_patterns USING gin (statement gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX idx_l3_patterns_salience "
        "ON l3_patterns (salience_score DESC, last_seen_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS l3_patterns")
