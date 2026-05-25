"""Embedding client wrapper — Phase C spec §5.7 (real) + §9.6b (mock).

Two paths:

  * **Real**: OpenAI ``text-embedding-3-small`` via the ``openai``
    SDK. 1536-d unit vectors. API key from ``OPENAI_API_KEY`` env.
    Timeout-bounded; per-item failure returns ``None`` for that item
    (the recall pipeline falls back to keyword for that candidate).

  * **Mock**: enabled via ``HERMES_RECALL_EMBEDDING_MOCK=1``. Uses
    SHA-256(input) to seed a deterministic pseudo-random 1536-d
    vector, then normalises to unit length. Two identical inputs
    produce identical vectors so ranker tests are stable. No
    network. Free.

The real path is constructed lazily — importing this module does NOT
require ``openai`` to be installed (the module-level imports stay clean).
The mock path is always available.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
from typing import Optional

from substrate import config as _substrate_config  # noqa: F401  (forward use)


# Dimension is pinned at the schema level (Alembic revision 0006 uses
# vector(1536)). Mismatch with the model's output dim raises loudly at
# the first embed call so silent drift can't corrupt rankings.
EMBEDDING_DIM = 1536
MOCK_ENV_VAR = "HERMES_RECALL_EMBEDDING_MOCK"
API_KEY_ENV_VAR = "OPENAI_API_KEY"


_client = None  # OpenAI client cache — created lazily.


def _is_mock_enabled() -> bool:
    raw = os.environ.get(MOCK_ENV_VAR, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _mock_embed_one(text: str) -> list[float]:
    """SHA-256-seeded deterministic mock. Unit-norm 1536-d vector.

    The hash digest is 32 bytes; we use a counter-mode expansion to
    derive ``EMBEDDING_DIM * 4`` bytes of entropy, interpret each
    4-byte block as a float in (-1, 1) via int-to-float mapping, then
    normalise.
    """
    base = hashlib.sha256(text.encode("utf-8")).digest()
    # Derive enough entropy: SHA-256 chained with counter index.
    needed = EMBEDDING_DIM * 4
    chunks: list[bytes] = []
    counter = 0
    while sum(len(c) for c in chunks) < needed:
        chunks.append(hashlib.sha256(base + counter.to_bytes(4, "big")).digest())
        counter += 1
    raw = b"".join(chunks)[:needed]
    # Map each 4-byte block to a float in (-1, 1).
    vec: list[float] = []
    for i in range(0, needed, 4):
        n = int.from_bytes(raw[i : i + 4], "big", signed=False)
        # Scale to (-1, 1). u32 range is [0, 2^32 - 1].
        vec.append((n / (2**32 - 1)) * 2.0 - 1.0)
    # Normalise to unit length.
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        # Defensive — astronomically unlikely with SHA-256.
        return [1.0 / math.sqrt(EMBEDDING_DIM)] * EMBEDDING_DIM
    return [x / norm for x in vec]


def _ensure_client():
    """Create the OpenAI AsyncClient on first use. Raises ``RuntimeError``
    if ``OPENAI_API_KEY`` is missing (the caller catches this and falls
    back to the keyword path)."""
    global _client
    if _client is not None:
        return _client
    if not os.environ.get(API_KEY_ENV_VAR):
        raise RuntimeError(
            f"{API_KEY_ENV_VAR} not set; cannot create embedding client"
        )
    # Late import — keeps recall package importable without openai if
    # only the mock path is in use.
    import openai

    _client = openai.AsyncOpenAI()
    return _client


def reset_client_cache() -> None:
    """Test seam — drop the cached client so a subsequent ``embed`` call
    re-reads the env vars and re-constructs the client."""
    global _client
    _client = None


async def embed(
    texts: list[str],
    *,
    model: str = "text-embedding-3-small",
    timeout_ms: int = 800,
) -> list[Optional[list[float]]]:
    """Return one 1536-d unit vector per input. None per-item on failure.

    Mock path: uses ``_mock_embed_one`` for each input. No network.
    Real path: single batch ``client.embeddings.create(...)`` call.

    On batch-level failure (network error, API error, timeout) returns
    a list of ``None`` of the right length so the caller can substitute
    keyword Jaccard for every item without special-casing.
    """
    if not texts:
        return []

    if _is_mock_enabled():
        return [_mock_embed_one(t) for t in texts]

    try:
        client = _ensure_client()
    except RuntimeError:
        # No API key → keyword fallback for the whole batch.
        return [None] * len(texts)

    try:
        resp = await asyncio.wait_for(
            client.embeddings.create(model=model, input=texts),
            timeout=timeout_ms / 1000.0,
        )
    except (asyncio.TimeoutError, Exception):
        # Any embedding-side error: caller falls back to keyword.
        return [None] * len(texts)

    out: list[Optional[list[float]]] = []
    for d in resp.data:
        vec = list(d.embedding)
        if len(vec) != EMBEDDING_DIM:
            # Dim guard — silent drift would corrupt rankings.
            raise RuntimeError(
                f"embedding dim mismatch: got {len(vec)}, expected {EMBEDDING_DIM} "
                f"(model={model!r}; check RECALL_EMBEDDING_MODEL config)"
            )
        out.append(vec)
    # Pad with None if the API returned fewer items than requested
    # (shouldn't happen but defensive).
    while len(out) < len(texts):
        out.append(None)
    return out


async def embed_query(
    query: str,
    *,
    model: str = "text-embedding-3-small",
    timeout_ms: int = 800,
) -> Optional[list[float]]:
    """Convenience wrapper for the recall pipeline. Returns the single
    embedding vector or None on failure (timeout / error / no key)."""
    if not query:
        return None
    out = await embed([query], model=model, timeout_ms=timeout_ms)
    if not out:
        return None
    return out[0]


__all__ = [
    "EMBEDDING_DIM",
    "MOCK_ENV_VAR",
    "API_KEY_ENV_VAR",
    "embed",
    "embed_query",
    "reset_client_cache",
]
