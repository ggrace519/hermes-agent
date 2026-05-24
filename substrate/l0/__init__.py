"""L0 — the perception substrate. Write-only public surface in Phase A.

The only public function is :func:`substrate.l0.api.commit_slice` (async)
and its sync facade :func:`commit_slice_sync` — both land in Task 7 of
the Phase A plan. Read endpoints (used by the Sentinel + force-reject
workers) live in ``substrate.storage.slices.SliceRepo`` and are not
exported here.
"""

__all__: list[str] = []
