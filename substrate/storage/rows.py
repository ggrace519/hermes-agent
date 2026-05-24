"""asyncpg.Record → dataclass converters.

asyncpg returns rows as ``Record`` objects with column-name lookup.
These helpers normalise the per-row result into the dataclasses defined
in :mod:`substrate.storage.types`. They live in their own module so
both the repos and the L0 API can share them without an awkward
import cycle.

The JSONB codec is registered at pool init (Phase 0 ADR), so JSONB
columns come back already decoded to ``dict`` / ``list`` — these
helpers never call ``json.loads`` themselves. INTERVAL columns come
back as :class:`datetime.timedelta`. UUID columns come back as
:class:`uuid.UUID`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from substrate.storage.types import (
    Address,
    ConsolidationState,
    DecayProfile,
    Family,
    Lifecycle,
    Modality,
    SentinelState,
    Slice,
    Stream,
    TombstonePolicy,
)

if TYPE_CHECKING:  # pragma: no cover
    import asyncpg


def _stream_from_row(row: "asyncpg.Record") -> Stream:
    """Materialise a :class:`Stream` from a ``substrate_streams`` row."""
    return Stream(
        stream_id=row["stream_id"],
        name=row["name"],
        family=Family(row["family"]),
        modality=Modality(row["modality"]),
        source=row["source"],
        organ=row["organ"],
        lifecycle_state=Lifecycle(row["lifecycle_state"]),
        decay_profile_id=row["decay_profile_id"],
        registered_at=row["registered_at"],
        retired_at=row["retired_at"],
        metadata=row["metadata"] or {},
    )


def _decay_profile_from_row(row: "asyncpg.Record") -> DecayProfile:
    """Materialise a :class:`DecayProfile` from a
    ``substrate_decay_profiles`` row. INTERVAL columns already arrive
    as :class:`datetime.timedelta` thanks to asyncpg's codec.
    """
    applies_to = row["applies_to_modality"]
    return DecayProfile(
        profile_id=row["profile_id"],
        name=row["name"],
        natural_half_life=row["natural_half_life"],
        consolidation_window=row["consolidation_window"],
        reinforcement_bump=row["reinforcement_bump"],
        min_salience_to_retain=row["min_salience_to_retain"],
        release_after_consolidation=row["release_after_consolidation"],
        summary_decay_multiplier=row["summary_decay_multiplier"],
        pending_ttl=row["pending_ttl"],
        tombstone_policy=TombstonePolicy(row["tombstone_policy"]),
        tombstone_none_justification=row["tombstone_none_justification"],
        applies_to_modality=Modality(applies_to) if applies_to else None,
    )


def _slice_from_row(row: "asyncpg.Record") -> Slice:
    """Materialise a :class:`Slice` from a ``substrate_slices`` row.

    ``summary_of`` is stored as JSONB array-of-objects with explicit
    ``{stream_id, t_start, t_end}`` keys; we walk it and build
    :class:`Address` instances. ``consolidated_to`` is JSONB array of
    strings (L1 entity ids) — kept as a plain list for Phase A since
    L1 doesn't exist yet.
    """
    summary_of_raw = row["summary_of"] or []
    summary_of = [
        Address(
            stream_id=item["stream_id"],
            time_start_world=item["t_start"],
            time_end_world=item["t_end"],
        )
        for item in summary_of_raw
    ]
    return Slice(
        slice_id=row["slice_id"],
        stream_id=row["stream_id"],
        time_start_world=row["time_start_world"],
        time_end_world=row["time_end_world"],
        event_time_world=row["event_time_world"],
        perception_time_world=row["perception_time_world"],
        ingest_time_world=row["ingest_time_world"],
        payload_modality=Modality(row["payload_modality"]),
        sentinel_state=SentinelState(row["sentinel_state"]),
        time_start_experiential=row["time_start_experiential"],
        time_end_experiential=row["time_end_experiential"],
        payload=row["payload"],
        payload_blob_ref=row["payload_blob_ref"],
        sentinel_reason=row["sentinel_reason"],
        pending_committed_at=row["pending_committed_at"],
        trust_score=row["trust_score"],
        salience_score=row["salience_score"],
        salience_updated_at=row["salience_updated_at"],
        summary_of=summary_of,
        consolidation_state=ConsolidationState(row["consolidation_state"]),
        consolidated_to=list(row["consolidated_to"] or []),
        metadata=row["metadata"] or {},
    )


__all__ = [
    "_decay_profile_from_row",
    "_slice_from_row",
    "_stream_from_row",
]
