"""Substrate dataclasses + enums.

Authoritative shape for in-memory representations of substrate rows.
The asyncpg row → dataclass conversions live in
:mod:`substrate.storage.rows`.

All ``datetime`` fields are **TZ-aware UTC** (PG ``TIMESTAMPTZ`` columns
are TZ-aware in asyncpg). Constructing a slice with a naive datetime is
a bug — :func:`substrate.l0.api.commit_slice` validates and raises
``TypeError``. ``INTERVAL`` columns map to ``datetime.timedelta``;
``JSONB`` columns map to plain ``dict``/``list`` (the pool's codec
handles encode/decode — never use ``::jsonb`` casts).

Enum string values are the **on-disk** values — they must match the
``CHECK`` constraints in the Alembic revision
``20260523_0003_substrate_skeleton`` exactly. If you rename one here,
rename it in the migration and add a follow-on revision; never let them
drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, Union
from uuid import UUID


# ---------------------------------------------------------------------------
# Enums — strings on disk; CHECK constraints in the migration mirror these.
# ---------------------------------------------------------------------------


class Family(str, Enum):
    """Stream family — where the perception originated.

    * ``exteroceptive``: signals coming from outside the agent (user
      messages, sensor reads).
    * ``self_action``: the agent's own outbound actions (assistant
      responses, tool calls).
    * ``self_state``: the agent's own internal state changes
      (session lifecycle, sub-agent decisions, force-reject audits).
    """

    EXTEROCEPTIVE = "exteroceptive"
    SELF_ACTION = "self_action"
    SELF_STATE = "self_state"


class Modality(str, Enum):
    """Payload shape. Determines how :func:`commit_slice` validates the
    ``payload`` argument.
    """

    TEXT = "text"
    STRUCTURED_EVENT = "structured_event"
    BINARY_BLOB = "binary_blob"
    SIGNAL = "signal"


class Lifecycle(str, Enum):
    """Stream lifecycle state. Only ``ACTIVE`` streams accept
    :func:`commit_slice` calls; the rest raise ``ValueError``.
    """

    REGISTERED = "registered"
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class SentinelState(str, Enum):
    """Sentinel decision state for a slice. Slices land as ``PENDING``
    and transition to ``PASSED`` (Phase A: every slice) or
    ``QUARANTINED`` (Phase B+: real defense logic).
    """

    PENDING = "pending"
    PASSED = "passed"
    QUARANTINED = "quarantined"


class ConsolidationState(str, Enum):
    """Forward-compat field for Phase B+ L1 consolidation handshake.
    Phase A leaves every slice at ``UNCONSOLIDATED``; the Curator
    transitions slices when it lands.
    """

    UNCONSOLIDATED = "unconsolidated"
    PARTIAL = "partial"
    CONSOLIDATED = "consolidated"
    RELEASED = "released"


class TombstonePolicy(str, Enum):
    """Decay-profile tombstone retention policy.

    * ``full``: keep slice_id + decision + reason after release.
    * ``thin``: keep slice_id only (no payload, no reason).
    * ``none``: drop the row entirely. Requires a justification string
      in the decay profile (CHECK constraint enforces it).
    """

    FULL = "full"
    THIN = "thin"
    NONE = "none"


# ---------------------------------------------------------------------------
# Dataclasses — match the column layout of the substrate_* tables.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Address:
    """A slice's logical address — what callers use to refer to a slice
    by content rather than by surrogate key.

    Three components because slices are time-bounded events on a stream:
    the stream identifies "where", and the start/end timestamps
    identify "when". The substrate uses the ``slice_id`` UUID for
    primary-key lookups internally; callers should treat ``Address`` as
    the public handle.
    """

    stream_id: UUID
    time_start_world: datetime
    time_end_world: datetime


@dataclass
class Stream:
    """A registered substrate stream — the source of a perception
    family. Stream metadata is write-rare / read-hot; ``StreamRepo``
    caches active streams in-process.
    """

    stream_id: UUID
    name: str
    family: Family
    modality: Modality
    source: str
    organ: str
    lifecycle_state: Lifecycle
    decay_profile_id: UUID
    registered_at: datetime
    retired_at: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class DecayProfile:
    """A bundle of decay / consolidation / tombstone settings applied to
    every stream that references it.

    INTERVAL columns map to :class:`datetime.timedelta`. The default
    profiles seeded by Alembic are referenced by stable v5 UUIDs so they
    can be looked up without a name round-trip.
    """

    profile_id: UUID
    name: str
    natural_half_life: timedelta
    consolidation_window: timedelta
    reinforcement_bump: float
    min_salience_to_retain: float
    release_after_consolidation: bool = True
    summary_decay_multiplier: float = 2.0
    pending_ttl: timedelta = field(default_factory=lambda: timedelta(seconds=30))
    tombstone_policy: TombstonePolicy = TombstonePolicy.THIN
    tombstone_none_justification: Optional[str] = None
    applies_to_modality: Optional[Modality] = None


@dataclass
class Slice:
    """A perception slice — the unit of substrate write.

    Field ordering matches the ``substrate_slices`` column layout (so
    ``rows._slice_from_row`` can populate by position when convenient).
    All ``datetime`` fields are TZ-aware UTC.
    """

    slice_id: UUID
    stream_id: UUID

    time_start_world: datetime
    time_end_world: datetime
    event_time_world: datetime
    perception_time_world: datetime
    ingest_time_world: datetime

    payload_modality: Modality
    sentinel_state: SentinelState

    time_start_experiential: Optional[datetime] = None
    time_end_experiential: Optional[datetime] = None
    payload: Optional[Union[str, dict]] = None
    payload_blob_ref: Optional[str] = None
    sentinel_reason: Optional[str] = None
    pending_committed_at: Optional[datetime] = None
    trust_score: Optional[float] = None
    salience_score: float = 1.0
    salience_updated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    summary_of: list[Address] = field(default_factory=list)
    consolidation_state: ConsolidationState = ConsolidationState.UNCONSOLIDATED
    consolidated_to: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def address(self) -> Address:
        """Return the logical address of this slice."""
        return Address(self.stream_id, self.time_start_world, self.time_end_world)
