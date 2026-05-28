"""substrate operational-telemetry sink (non-perceptual)

Append-only table for the substrate's own operational events — the
decisions its sub-agents make about *running the substrate* (Conductor
dials, Sentinel batch summaries, Curator releases/alarms, Reflector /
Dreamer / Critic / Associator / PatternFinder / Summarizer / Parser
activity, force-reject audits).

These used to be written as slices on the perceptual ``substrate.self_state``
stream. That fed a self-sustaining feedback loop: every operational event
landed in L0 as a ``passed + unconsolidated`` slice, which the Conductor
counted as consolidation backlog (``_read_load``), so it pinned the Parser
HIGH and emitted *another* ``conductor.dialed`` slice — but the Parser
could never drain them (they carry no ``session_id``), so the backlog never
fell. 2026-05-26→27 prod incident: 414k ``substrate.self_state`` slices.

This table is the non-perceptual sink: the substrate writes to it but the
awareness loop never reads from it (no ``substrate_slices`` row → never
counted by backlog/consolidation/recall/Sentinel). The schema-level
boundary is ``substrate.storage.streams.is_perceptual`` (substrate.*
streams are excluded from every awareness-loop query); this table is the
positive destination for what that boundary excludes.

Per the L0-feedback-loop fix. Mirrors the per-agent ``substrate_*_log``
observability tables (parser/dreamer/conductor) but is the general sink.

Revision ID: 20260527_0019
Revises: 20260527_0018
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op


revision = "20260527_0019"
down_revision = "20260527_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE substrate_telemetry (
            id       BIGSERIAL   PRIMARY KEY,
            at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            agent    TEXT        NOT NULL,
            event    TEXT        NOT NULL,
            payload  JSONB       NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    # Recent-first scans (operator "tail"-style inspect commands).
    op.execute(
        "CREATE INDEX idx_substrate_telemetry_recent "
        "ON substrate_telemetry (at DESC)"
    )
    # Per-event-kind rollups (e.g. inspect curator reads event LIKE 'curator.%').
    op.execute(
        "CREATE INDEX idx_substrate_telemetry_event "
        "ON substrate_telemetry (event, at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS substrate_telemetry")
