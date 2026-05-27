"""substrate L4 — self-model / calibration observations (Phase F core)

L4 is what the substrate knows about *itself* — its calibration and
coherence (MVS §3.2 "L4: meta-cognition / self-model / calibration").
The Critic writes ``l4_observations``: calibration notes about sub-agents
(e.g. Parser reliability), and a cross-layer **coherence** score — the
substrate's vital sign (MVS §3.7).

Append-only (history matters: drift is visible across observations).

Per the Phase F core spec (2026-05-27-phase-f-l4-critic.md). The full L4
(LLM Reflector synthesis, Dreamer, learned-Conductor policy) is deferred
research per the MVS spec's own deferrals.

Revision ID: 20260527_0015
Revises: 20260527_0014
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op


revision = "20260527_0015"
down_revision = "20260527_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE l4_observations (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            kind        TEXT NOT NULL
                CHECK (kind IN ('coherence','calibration','bias','other')),
            subject     TEXT NOT NULL,
            statement   TEXT NOT NULL,
            score       DOUBLE PRECISION,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            metadata    JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute("CREATE INDEX idx_l4_recent ON l4_observations (created_at DESC)")
    op.execute(
        "CREATE INDEX idx_l4_subject ON l4_observations (subject, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_l4_kind ON l4_observations (kind, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS l4_observations")
