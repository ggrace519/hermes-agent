"""kanban schema

Revision ID: 20260522_0002
Revises: 20260522_0001
Create Date: 2026-05-22
"""
from alembic import op

revision = "20260522_0002"
down_revision = "20260522_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Board registry: one row per named board.
    # The 'default' board is always present (inserted below).
    op.execute(
        """
        CREATE TABLE kanban_boards (
            slug        TEXT PRIMARY KEY,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("INSERT INTO kanban_boards (slug) VALUES ('default')")

    # Main tasks table — all columns ported verbatim from upstream SCHEMA_SQL
    # plus additive-migration columns (_migrate_add_optional_columns).
    # INTEGER epoch columns stay as INTEGER (unix timestamps).
    # skills (JSON array) → JSONB.  payload in task_events → JSONB.
    # metadata in task_runs → JSONB.
    op.execute(
        """
        CREATE TABLE kanban_tasks (
            id                   TEXT NOT NULL,
            board_slug           TEXT NOT NULL REFERENCES kanban_boards(slug) ON DELETE CASCADE,
            title                TEXT NOT NULL,
            body                 TEXT,
            assignee             TEXT,
            status               TEXT NOT NULL,
            priority             INTEGER NOT NULL DEFAULT 0,
            created_by           TEXT,
            created_at           INTEGER NOT NULL,
            started_at           INTEGER,
            completed_at         INTEGER,
            workspace_kind       TEXT NOT NULL DEFAULT 'scratch',
            workspace_path       TEXT,
            branch_name          TEXT,
            claim_lock           TEXT,
            claim_expires        INTEGER,
            tenant               TEXT,
            result               TEXT,
            idempotency_key      TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            worker_pid           INTEGER,
            last_failure_error   TEXT,
            max_runtime_seconds  INTEGER,
            last_heartbeat_at    INTEGER,
            current_run_id       BIGINT,
            workflow_template_id TEXT,
            current_step_key     TEXT,
            skills               JSONB,
            model_override       TEXT,
            max_retries          INTEGER,
            session_id           TEXT,
            PRIMARY KEY (id, board_slug)
        )
        """
    )
    # Surrogate unique index so foreign keys from task_runs / task_links / etc.
    # can reference just `id` within a board context.  We use a composite PK
    # (id, board_slug) to allow the same logical task id to exist in different
    # boards without a global uniqueness constraint.  task_links and comments
    # carry the board_slug themselves for isolation.
    op.execute(
        "CREATE UNIQUE INDEX kanban_tasks_id_board_idx ON kanban_tasks (id, board_slug)"
    )
    op.execute(
        "CREATE INDEX kanban_tasks_board_status_idx ON kanban_tasks (board_slug, status)"
    )
    op.execute(
        "CREATE INDEX kanban_tasks_assignee_status_idx ON kanban_tasks (board_slug, assignee, status)"
    )
    op.execute(
        "CREATE INDEX kanban_tasks_tenant_idx ON kanban_tasks (board_slug, tenant)"
    )
    op.execute(
        "CREATE INDEX kanban_tasks_idempotency_idx ON kanban_tasks (board_slug, idempotency_key)"
    )
    op.execute(
        "CREATE INDEX kanban_tasks_session_id_idx ON kanban_tasks (board_slug, session_id)"
    )

    op.execute(
        """
        CREATE TABLE kanban_task_links (
            board_slug  TEXT NOT NULL REFERENCES kanban_boards(slug) ON DELETE CASCADE,
            parent_id   TEXT NOT NULL,
            child_id    TEXT NOT NULL,
            PRIMARY KEY (board_slug, parent_id, child_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX kanban_task_links_child_idx ON kanban_task_links (board_slug, child_id)"
    )
    op.execute(
        "CREATE INDEX kanban_task_links_parent_idx ON kanban_task_links (board_slug, parent_id)"
    )

    op.execute(
        """
        CREATE TABLE kanban_task_comments (
            id         BIGSERIAL PRIMARY KEY,
            board_slug TEXT NOT NULL REFERENCES kanban_boards(slug) ON DELETE CASCADE,
            task_id    TEXT NOT NULL,
            author     TEXT NOT NULL,
            body       TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX kanban_task_comments_task_idx ON kanban_task_comments (board_slug, task_id, created_at)"
    )

    op.execute(
        """
        CREATE TABLE kanban_task_events (
            id         BIGSERIAL PRIMARY KEY,
            board_slug TEXT NOT NULL REFERENCES kanban_boards(slug) ON DELETE CASCADE,
            task_id    TEXT NOT NULL,
            run_id     BIGINT,
            kind       TEXT NOT NULL,
            payload    JSONB,
            created_at INTEGER NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX kanban_task_events_task_idx ON kanban_task_events (board_slug, task_id, created_at)"
    )
    op.execute(
        "CREATE INDEX kanban_task_events_run_idx ON kanban_task_events (run_id, id)"
    )

    # Historical attempt records per task.
    op.execute(
        """
        CREATE TABLE kanban_task_runs (
            id                  BIGSERIAL PRIMARY KEY,
            board_slug          TEXT NOT NULL REFERENCES kanban_boards(slug) ON DELETE CASCADE,
            task_id             TEXT NOT NULL,
            profile             TEXT,
            step_key            TEXT,
            status              TEXT NOT NULL,
            claim_lock          TEXT,
            claim_expires       INTEGER,
            worker_pid          INTEGER,
            max_runtime_seconds INTEGER,
            last_heartbeat_at   INTEGER,
            started_at          INTEGER NOT NULL,
            ended_at            INTEGER,
            outcome             TEXT,
            summary             TEXT,
            metadata            JSONB,
            error               TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX kanban_task_runs_task_idx ON kanban_task_runs (board_slug, task_id, started_at)"
    )
    op.execute(
        "CREATE INDEX kanban_task_runs_status_idx ON kanban_task_runs (board_slug, status)"
    )

    # Gateway notification subscriptions per board.
    op.execute(
        """
        CREATE TABLE kanban_notify_subs (
            board_slug       TEXT NOT NULL REFERENCES kanban_boards(slug) ON DELETE CASCADE,
            task_id          TEXT NOT NULL,
            platform         TEXT NOT NULL,
            chat_id          TEXT NOT NULL,
            thread_id        TEXT NOT NULL DEFAULT '',
            user_id          TEXT,
            notifier_profile TEXT,
            created_at       INTEGER NOT NULL,
            last_event_id    BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (board_slug, task_id, platform, chat_id, thread_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX kanban_notify_subs_task_idx ON kanban_notify_subs (board_slug, task_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS kanban_notify_subs CASCADE")
    op.execute("DROP TABLE IF EXISTS kanban_task_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS kanban_task_events CASCADE")
    op.execute("DROP TABLE IF EXISTS kanban_task_comments CASCADE")
    op.execute("DROP TABLE IF EXISTS kanban_task_links CASCADE")
    op.execute("DROP TABLE IF EXISTS kanban_tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS kanban_boards CASCADE")
