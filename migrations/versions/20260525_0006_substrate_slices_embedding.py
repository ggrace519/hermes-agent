"""substrate slices embedding column — Phase C semantic recall

Enables the ``vector`` extension (pgvector — provided by the
``pgvector/pgvector:pg17`` container image but not auto-enabled in the
database) and adds a 1536-d ``embedding`` column + ivfflat cosine-ops
index to ``substrate_slices``. The dimension is pinned at the schema
level so cross-model swaps (e.g. moving to a 3072-d model) require an
explicit migration — preventing silent dimension drift.

The Curator backfills embeddings on a cadence (Phase C spec §5.7);
this migration adds the column NULL-by-default and does not backfill.
Recall against unembedded slices falls back to keyword-Jaccard ranking
until the Curator catches up.

``lists = 100`` is the conservative ivfflat default for the
~10k–100k-slice operator-validation regime (pgvector's
``lists ≈ sqrt(rows)`` guidance). Phase C does not auto-tune; once
the substrate routinely holds 100k+ slices a follow-up REINDEX with
larger ``lists`` is in order.

Per the Phase C spec §3.3:
  - Downgrade drops the index then the column, but does NOT drop the
    ``vector`` extension — other tables or future migrations may use it.
  - The new index is on the parent table; PG 17 propagates it to every
    present and future partition automatically (mirrors the Phase A
    skeleton's index strategy).

Revision ID: 20260525_0006
Revises: 20260525_0005
Create Date: 2026-05-25
"""
from __future__ import annotations

from alembic import op


revision = "20260525_0006"
down_revision = "20260525_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        ALTER TABLE substrate_slices
            ADD COLUMN embedding vector(1536)
        """
    )
    op.execute(
        """
        CREATE INDEX substrate_slices_embedding_cosine_idx
            ON substrate_slices
            USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS substrate_slices_embedding_cosine_idx"
    )
    op.execute(
        """
        ALTER TABLE substrate_slices
            DROP COLUMN IF EXISTS embedding
        """
    )
    # Note: do NOT drop the `vector` extension in downgrade — other
    # tables or migrations may use it later.
