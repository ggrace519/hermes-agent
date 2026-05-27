"""substrate dreamer exploration log (Phase F)

The Dreamer roams the substrate in counterfactual mode and records its
explorations to ``substrate_dreamer_log`` — a persistent log that survives
restarts (MVS §3.8: "Dreamer's exploration log is checkpointed"; "Dreamer
projects persist across sleep cycles"). The mind has intellectual life the
foreground doesn't witness.

Per the Phase F Dreamer spec (2026-05-27-phase-f-dreamer.md).

Revision ID: 20260527_0016
Revises: 20260527_0015
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op


revision = "20260527_0016"
down_revision = "20260527_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE substrate_dreamer_log (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            seed         TEXT NOT NULL,
            exploration  TEXT NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            metadata     JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_dreamer_recent ON substrate_dreamer_log (created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS substrate_dreamer_log")
