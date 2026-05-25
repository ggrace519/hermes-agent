"""substrate recall log — Phase C observability

Adds ``substrate_recall_log`` for per-call recall observability and for
the recall-reinforcement budgeting feedback loop. Tiny table (~one row
per turn) with a single covering index on ``(session_id,
requested_at DESC)`` for the inspect-CLI's recent-sample query.

Per the Phase C spec §3.2:
  - Writes happen out-of-band from the recall hot path via a bounded
    async background writer; if the writer's queue is full a row is
    dropped silently.
  - JSONB metadata is encoded by the pool's codec — *never* use
    ``::jsonb`` casts (corrupts the prepared-statement type cache per
    the Phase 0 ADR).
  - Retention is application-level (the inspect CLI does ORDER BY
    requested_at DESC LIMIT N); no PG-side retention partitioning.

Revision ID: 20260525_0005
Revises: 20260524_0004
Create Date: 2026-05-25
"""
from __future__ import annotations

from alembic import op


revision = "20260525_0005"
down_revision = "20260524_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE substrate_recall_log (
            log_id            BIGSERIAL    PRIMARY KEY,
            requested_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            session_id        TEXT,
            query_excerpt     TEXT,
            candidates_count  INTEGER      NOT NULL,
            composed_count    INTEGER      NOT NULL,
            tokens_used       INTEGER      NOT NULL,
            duration_ms       INTEGER      NOT NULL,
            timed_out         BOOLEAN      NOT NULL DEFAULT FALSE,
            error_text        TEXT,
            metadata          JSONB        NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute(
        """
        CREATE INDEX substrate_recall_log_session_time_idx
            ON substrate_recall_log (session_id, requested_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS substrate_recall_log_session_time_idx")
    op.execute("DROP TABLE IF EXISTS substrate_recall_log")
