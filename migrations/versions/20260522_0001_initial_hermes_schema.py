"""initial hermes schema

Revision ID: 20260522_0001
Revises:
Create Date: 2026-05-22
"""
from alembic import op
import sqlalchemy as sa

revision = "20260522_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            user_id TEXT,
            model TEXT,
            model_config JSONB,
            system_prompt TEXT,
            parent_session_id TEXT REFERENCES sessions(id),
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            ended_at TIMESTAMPTZ,
            end_reason TEXT,
            message_count INTEGER NOT NULL DEFAULT 0,
            tool_call_count INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            billing_provider TEXT,
            billing_base_url TEXT,
            billing_mode TEXT,
            estimated_cost_usd DOUBLE PRECISION,
            actual_cost_usd DOUBLE PRECISION,
            cost_status TEXT,
            cost_source TEXT,
            pricing_version TEXT,
            title TEXT,
            api_call_count INTEGER NOT NULL DEFAULT 0,
            handoff_state TEXT,
            handoff_platform TEXT,
            handoff_error TEXT
        )
        """
    )
    op.execute("CREATE INDEX sessions_source_idx        ON sessions (source)")
    op.execute("CREATE INDEX sessions_parent_idx        ON sessions (parent_session_id)")
    op.execute("CREATE INDEX sessions_started_at_idx    ON sessions (started_at DESC)")
    op.execute(
        "CREATE INDEX sessions_handoff_state_idx ON sessions (handoff_state) "
        "WHERE handoff_state IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE messages (
            id BIGSERIAL PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT,
            tool_call_id TEXT,
            tool_calls JSONB,
            tool_name TEXT,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
            token_count INTEGER,
            finish_reason TEXT,
            reasoning TEXT,
            reasoning_content TEXT,
            reasoning_details JSONB,
            codex_reasoning_items JSONB,
            codex_message_items JSONB,
            platform_message_id TEXT,
            content_tsv tsvector GENERATED ALWAYS AS (
                to_tsvector('english',
                    coalesce(content, '') || ' ' ||
                    coalesce(tool_name, '') || ' ' ||
                    coalesce(tool_calls::text, '')
                )
            ) STORED
        )
        """
    )
    op.execute("CREATE INDEX messages_session_ts_idx  ON messages (session_id, timestamp)")
    op.execute("CREATE INDEX messages_tsv_idx         ON messages USING GIN (content_tsv)")
    op.execute("CREATE INDEX messages_trgm_idx        ON messages USING GIN (content gin_trgm_ops)")
    op.execute(
        "CREATE INDEX messages_platform_id_idx ON messages (platform_message_id) "
        "WHERE platform_message_id IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE state_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS messages CASCADE")
    op.execute("DROP TABLE IF EXISTS sessions CASCADE")
    op.execute("DROP TABLE IF EXISTS state_meta CASCADE")
    # Extensions remain installed (other migrations / substrate may rely on them)
