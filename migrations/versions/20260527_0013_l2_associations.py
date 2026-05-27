"""substrate L2 — associations + edit history (Phase E1)

The associative graph above L1. The Associator sub-agent weaves weighted,
typed edges between L1 entities — discovered structure, distinct from the
explicit L1 relationships the Parser extracts:

* ``co_occurrence`` — two entities cited by the same L0 slice.
* ``shared_neighbor`` — two entities sharing a relationship partner.

Edges are undirected, canonicalised to ``src_id < dst_id`` (one row per
pair per type). ``substrate_association_edits`` is the append-only history
of every weight change — the design's "L2 with edit history" (MVS §3.2),
and the raw material for the Critic's L2-vs-salience coherence audit
(MVS §3.7/§5.6, Phase F).

Per the Phase E1 spec (2026-05-27-phase-e1-l2-associator.md) §1.

Revision ID: 20260527_0013
Revises: 20260527_0012
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op


revision = "20260527_0013"
down_revision = "20260527_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE substrate_associations (
            assoc_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            src_id      UUID NOT NULL REFERENCES l1_entities(id) ON DELETE CASCADE,
            dst_id      UUID NOT NULL REFERENCES l1_entities(id) ON DELETE CASCADE,
            edge_type   TEXT NOT NULL
                CHECK (edge_type IN ('co_occurrence','shared_neighbor')),
            weight      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
            UNIQUE (src_id, dst_id, edge_type),
            CHECK (src_id < dst_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_assoc_src ON substrate_associations (src_id, weight DESC)"
    )
    op.execute(
        "CREATE INDEX idx_assoc_dst ON substrate_associations (dst_id, weight DESC)"
    )

    op.execute(
        """
        CREATE TABLE substrate_association_edits (
            edit_id    BIGSERIAL PRIMARY KEY,
            assoc_id   UUID NOT NULL
                REFERENCES substrate_associations(assoc_id) ON DELETE CASCADE,
            at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            old_weight DOUBLE PRECISION,
            new_weight DOUBLE PRECISION NOT NULL,
            reason     TEXT NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_assoc_edit ON substrate_association_edits (assoc_id, at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS substrate_association_edits")
    op.execute("DROP TABLE IF EXISTS substrate_associations")
