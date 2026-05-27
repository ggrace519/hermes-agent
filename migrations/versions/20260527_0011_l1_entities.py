"""substrate L1 — entities, relationships, citations (Phase D)

First structured layer above L0. The Parser sub-agent (Phase D) distils
``passed`` L0 slices into:

* ``l1_entities`` — named things that recur (people, projects, files,
  concepts, places, orgs), merged on ``(name, entity_type)``.
* ``l1_relationships`` — typed directed links between entities, deduped
  on ``(subject_id, predicate, object_id)``.
* ``l1_citations`` — the grounding: which L0 slice each entity /
  relationship was extracted from, plus the exact quote. This is the
  citation backbone the design (§4.6, §5.5) leans on — it stays valid
  after the Curator releases the source slice (tombstone keeps the row).

Per the Phase D spec (2026-05-25-phase-d-l1-parser.md) §3.

**Deltas from the spec's SQL, forced by reality (documented here so the
ADR can reference them):**

1. ``l1_citations.slice_id`` is a plain indexed ``UUID`` with **no foreign
   key** to ``substrate_slices``. The spec wrote
   ``REFERENCES substrate_slices(slice_id) ON DELETE RESTRICT``, but
   ``substrate_slices`` is RANGE-partitioned with a *composite* primary key
   ``(slice_id, ingest_time_world)`` (Phase A) — PG cannot create a foreign
   key against ``slice_id`` alone because it is not unique on its own.
   Citations therefore reference the slice by id logically; the slice row
   persists (tombstoned, never DELETEd) under Curator policy, so the
   grounding-preservation intent is met without the DB-level FK.
2. Revision numbering: the spec named these ``0007``/``0008`` (drafted
   before Phase C's ``0006`` → configurable-dim ``0009`` → heartbeat
   ``0010`` landed). The actual chain is ``…0010 → 0011 → 0012``.

Revision ID: 20260527_0011
Revises: 20260527_0010
Create Date: 2026-05-27
"""
from __future__ import annotations

from alembic import op


revision = "20260527_0011"
down_revision = "20260527_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # gen_random_uuid() is built-in from PG 13+; pg_trgm enabled in Phase 0.
    op.execute(
        """
        CREATE TABLE l1_entities (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name                 TEXT NOT NULL,
            entity_type          TEXT NOT NULL
                CHECK (entity_type IN
                    ('person','project','file','concept','place','org','other')),
            summary              TEXT NOT NULL DEFAULT '',
            aliases              TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
            salience_score       DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            salience_updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            extra                JSONB NOT NULL DEFAULT '{}'::jsonb,
            UNIQUE (name, entity_type)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_l1_entities_name_trgm "
        "ON l1_entities USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX idx_l1_entities_type "
        "ON l1_entities (entity_type, last_seen_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_l1_entities_salience "
        "ON l1_entities (salience_score DESC, last_seen_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE l1_relationships (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            subject_id    UUID NOT NULL REFERENCES l1_entities(id) ON DELETE CASCADE,
            predicate     TEXT NOT NULL,
            object_id     UUID NOT NULL REFERENCES l1_entities(id) ON DELETE CASCADE,
            confidence    DOUBLE PRECISION NOT NULL DEFAULT 0.7,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            extra         JSONB NOT NULL DEFAULT '{}'::jsonb,
            UNIQUE (subject_id, predicate, object_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_l1_rel_subject "
        "ON l1_relationships (subject_id, last_seen_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_l1_rel_object "
        "ON l1_relationships (object_id, last_seen_at DESC)"
    )

    # Polymorphic citation: exactly one of entity_id / relationship_id set.
    # slice_id is a plain UUID (no FK — see module docstring delta #1).
    op.execute(
        """
        CREATE TABLE l1_citations (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_id       UUID REFERENCES l1_entities(id) ON DELETE CASCADE,
            relationship_id UUID REFERENCES l1_relationships(id) ON DELETE CASCADE,
            slice_id        UUID NOT NULL,
            quote           TEXT NOT NULL DEFAULT '',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK ((entity_id IS NULL) <> (relationship_id IS NULL))
        )
        """
    )
    op.execute("CREATE INDEX idx_l1_cite_entity ON l1_citations (entity_id)")
    op.execute("CREATE INDEX idx_l1_cite_rel ON l1_citations (relationship_id)")
    op.execute("CREATE INDEX idx_l1_cite_slice ON l1_citations (slice_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS l1_citations")
    op.execute("DROP TABLE IF EXISTS l1_relationships")
    op.execute("DROP TABLE IF EXISTS l1_entities")
