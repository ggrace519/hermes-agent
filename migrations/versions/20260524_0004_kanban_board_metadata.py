"""kanban board metadata columns

Adds the metadata columns (name, description, icon, color,
default_workdir, archived_at) to kanban_boards so the boards CRUD
surface — previously backed by ``boards/<slug>/board.json`` files —
can be fully PG-resident. Replaces the filesystem-scan-based
``list_boards`` / ``read_board_metadata`` / ``remove_board`` paths.

Revision ID: 20260524_0004
Revises: 20260523_0003
Create Date: 2026-05-24
"""
from alembic import op

revision = "20260524_0004"
down_revision = "20260523_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE kanban_boards
            ADD COLUMN name             TEXT,
            ADD COLUMN description      TEXT,
            ADD COLUMN icon             TEXT,
            ADD COLUMN color            TEXT,
            ADD COLUMN default_workdir  TEXT,
            ADD COLUMN archived_at      TIMESTAMPTZ
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE kanban_boards
            DROP COLUMN IF EXISTS archived_at,
            DROP COLUMN IF EXISTS default_workdir,
            DROP COLUMN IF EXISTS color,
            DROP COLUMN IF EXISTS icon,
            DROP COLUMN IF EXISTS description,
            DROP COLUMN IF EXISTS name
        """
    )
