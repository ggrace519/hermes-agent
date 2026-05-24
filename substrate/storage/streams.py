"""StreamRepo — registration and lookup for ``substrate_streams``.

Streams are write-rare / read-hot: a single Hermes session emits
hundreds of slices per minute against a handful of streams, and each
:func:`commit_slice` call needs to know the stream's modality + lifecycle
state. So this repo caches every fetched stream in a bounded in-process
dict keyed by both ``stream_id`` and ``name``. ``invalidate()`` is
called explicitly when a lifecycle change happens (Phase B+ may flip
streams to ``paused`` or ``retired``).

The cache is intentionally small (256 entries) — Phase A has 15
auto-registered streams and a handful of dynamically-registered ones;
the bound is more about catching runaway-stream bugs than memory.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from substrate.storage.rows import _stream_from_row
from substrate.storage.types import Family, Lifecycle, Modality, Stream

if TYPE_CHECKING:  # pragma: no cover
    import asyncpg


_MAX_CACHE_ENTRIES = 256


class StreamRepo:
    """Registration + lookup for substrate streams.

    Holds a reference to the shared ``hermes_db.pool()`` for read-only
    methods. Methods that participate in caller-controlled transactions
    accept an explicit ``conn``.
    """

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool
        # OrderedDict preserves insertion order, which lets us treat it
        # as a bounded LRU (move-to-end on read, pop-front on overflow).
        self._cache: OrderedDict[UUID, Stream] = OrderedDict()
        self._by_name: dict[str, UUID] = {}

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(
        self,
        stream_id: UUID,
        *,
        conn: "Optional[asyncpg.Connection]" = None,
    ) -> Optional[Stream]:
        """Return the stream by id, hitting the cache first."""
        cached = self._cache.get(stream_id)
        if cached is not None:
            self._cache.move_to_end(stream_id)
            return cached

        row = await self._fetchrow(
            "SELECT * FROM substrate_streams WHERE stream_id = $1",
            stream_id,
            conn=conn,
        )
        if row is None:
            return None
        stream = _stream_from_row(row)
        self._remember(stream)
        return stream

    async def get_by_name(
        self,
        name: str,
        *,
        conn: "Optional[asyncpg.Connection]" = None,
    ) -> Optional[Stream]:
        """Return the stream by unique name. Cache lookup goes through
        ``_by_name`` to ``get()``."""
        cached_id = self._by_name.get(name)
        if cached_id is not None:
            cached = self._cache.get(cached_id)
            if cached is not None:
                self._cache.move_to_end(cached_id)
                return cached

        row = await self._fetchrow(
            "SELECT * FROM substrate_streams WHERE name = $1",
            name,
            conn=conn,
        )
        if row is None:
            return None
        stream = _stream_from_row(row)
        self._remember(stream)
        return stream

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def register(
        self,
        *,
        name: str,
        family: Family,
        modality: Modality,
        source: str,
        organ: str,
        decay_profile_id: UUID,
        lifecycle_state: Lifecycle = Lifecycle.ACTIVE,
        metadata: Optional[dict] = None,
        conn: "Optional[asyncpg.Connection]" = None,
    ) -> Stream:
        """Idempotently register a stream.

        ``INSERT ... ON CONFLICT (name) DO NOTHING RETURNING`` returns
        the inserted row on first registration. On a name collision the
        ``RETURNING`` clause emits zero rows, so we fall back to a
        ``SELECT`` to fetch the existing stream. Either way the caller
        gets a populated :class:`Stream`.
        """
        meta = metadata or {}

        async def _do(c: "asyncpg.Connection") -> Stream:
            inserted = await c.fetchrow(
                """
                INSERT INTO substrate_streams
                    (name, family, modality, source, organ,
                     lifecycle_state, decay_profile_id, metadata)
                VALUES
                    ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (name) DO NOTHING
                RETURNING *
                """,
                name,
                family.value,
                modality.value,
                source,
                organ,
                lifecycle_state.value,
                decay_profile_id,
                meta,
            )
            if inserted is not None:
                return _stream_from_row(inserted)
            # Existed already — fetch it.
            existing = await c.fetchrow(
                "SELECT * FROM substrate_streams WHERE name = $1", name
            )
            assert existing is not None  # ON CONFLICT guarantees existence
            return _stream_from_row(existing)

        if conn is not None:
            stream = await _do(conn)
        else:
            async with self._pool.acquire() as c:
                stream = await _do(c)
        self._remember(stream)
        return stream

    def invalidate(self, stream_id: UUID) -> None:
        """Drop a stream from the cache. Called when a lifecycle
        transition (active → paused, etc.) makes the cached entry
        stale."""
        cached = self._cache.pop(stream_id, None)
        if cached is not None:
            # Maintain the name → id reverse map in lockstep.
            self._by_name.pop(cached.name, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _remember(self, stream: Stream) -> None:
        """Insert into the bounded LRU + name index. Evicts the LRU
        entry if the cache is at the bound."""
        if len(self._cache) >= _MAX_CACHE_ENTRIES and stream.stream_id not in self._cache:
            evicted_id, evicted = self._cache.popitem(last=False)
            self._by_name.pop(evicted.name, None)
        self._cache[stream.stream_id] = stream
        self._cache.move_to_end(stream.stream_id)
        self._by_name[stream.name] = stream.stream_id

    async def _fetchrow(
        self,
        query: str,
        *args,
        conn: "Optional[asyncpg.Connection]" = None,
    ):
        if conn is not None:
            return await conn.fetchrow(query, *args)
        async with self._pool.acquire() as c:
            return await c.fetchrow(query, *args)
