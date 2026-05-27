"""substrate slice pinning — decay-immune memories

Adds ``substrate_slices.pinned``. A pinned slice is exempt from the
Curator's natural decay and from release — the operator's "this matters,
never forget it" override (manual importance). Reinforcement-on-recall
already provides the
*automatic* importance signal (frequently-recalled slices keep high
salience); pinning is the *manual* one.

Strictly additive: defaults FALSE, so existing behaviour is unchanged
until something pins a slice. The Curator's decay UPDATE + release scan
gain an ``AND NOT pinned`` guard.

Revision ID: 20260527_0018
Revises: 20260527_0017
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op


revision = "20260527_0018"
down_revision = "20260527_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD COLUMN on the partitioned parent propagates to all partitions.
    op.execute(
        "ALTER TABLE substrate_slices "
        "ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT FALSE"
    )
    # Partial index: pinned slices are rare; this keeps the curator's
    # "skip pinned" filter and the inspect "list pinned" query cheap.
    op.execute(
        "CREATE INDEX idx_slices_pinned ON substrate_slices (pinned) WHERE pinned"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_slices_pinned")
    op.execute("ALTER TABLE substrate_slices DROP COLUMN IF EXISTS pinned")
