"""L2 — the associative graph over L1 entities.

The Associator sub-agent (Phase E1) weaves weighted, typed associations
between L1 entities — discovered structure (co-occurrence, shared
neighbour), distinct from the explicit relationships the Parser extracts.
Every weight change is recorded in an append-only edit history.
"""

from __future__ import annotations

from substrate.l2.schema import Association, AssociationEdit

__all__ = ["Association", "AssociationEdit"]
