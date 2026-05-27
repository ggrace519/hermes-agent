"""substrate conductor decision log (Phase F — learned Conductor)

Persists the Conductor's intensity decisions + the backlog it observed and
its forecast (EMA) at decision time. Two purposes: (1) the Conductor seeds
its forecast from this history on boot, so its learned rhythm survives
restarts (MVS §3.6 "its learned rhythms are themselves part of the mind");
(2) operators (and a future Critic audit of the Conductor) can see what it
decided and why.

Per the Phase F learned-Conductor spec (2026-05-27-phase-f-learned-conductor.md).

Revision ID: 20260527_0017
Revises: 20260527_0016
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op


revision = "20260527_0017"
down_revision = "20260527_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE substrate_conductor_log (
            id            BIGSERIAL PRIMARY KEY,
            at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            backlog_ratio DOUBLE PRECISION NOT NULL,
            forecast      DOUBLE PRECISION NOT NULL,
            targets       JSONB NOT NULL DEFAULT '{}'::jsonb,
            metadata      JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_conductor_log_recent ON substrate_conductor_log (at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS substrate_conductor_log")
