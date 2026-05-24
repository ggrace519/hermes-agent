"""Substrate storage layer — repositories over the shared asyncpg pool.

All repos are async and accept an explicit ``asyncpg.Connection`` so the
caller controls transactions. Repos hold a ``hermes_db.pool()`` reference
only for the read-only, no-txn-needed methods (e.g. ``StreamRepo.get``).

Phase A build status: types + enums are present; repositories land in
Task 6 of the Phase A plan and start exporting once they exist.
"""

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

__all__ = [
    "Address",
    "ConsolidationState",
    "DecayProfile",
    "Family",
    "Lifecycle",
    "Modality",
    "SentinelState",
    "Slice",
    "Stream",
    "TombstonePolicy",
]
