"""Embedding client tests — Phase C Task 6b / spec §9.6b."""

from __future__ import annotations

import asyncio
import math
import os

import pytest

from substrate.recall import embeddings


@pytest.fixture(autouse=True)
def _enable_mock(monkeypatch):
    """Default every test to the mock path; tests that need real may opt out."""
    monkeypatch.setenv(embeddings.MOCK_ENV_VAR, "1")
    embeddings.reset_client_cache()


@pytest.mark.asyncio
async def test_embed_mock_path_deterministic():
    """Two calls with the same input return identical vectors."""
    a = await embeddings.embed(["hello"])
    b = await embeddings.embed(["hello"])
    assert a == b
    assert len(a) == 1
    assert a[0] is not None


@pytest.mark.asyncio
async def test_embed_mock_vector_dim():
    """Mock vector length matches the pinned dim (1536)."""
    out = await embeddings.embed(["x"])
    assert out[0] is not None
    assert len(out[0]) == embeddings.EMBEDDING_DIM


@pytest.mark.asyncio
async def test_embed_mock_unit_norm():
    """Mock vector has L2 norm ≈ 1.0 (so cosine == dot product)."""
    out = await embeddings.embed(["unit test"])
    vec = out[0]
    norm = math.sqrt(sum(x * x for x in vec))
    assert norm == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_embed_batch_returns_list_of_vectors():
    """embed(['a', 'b']) → 2 vectors, preserves order."""
    out = await embeddings.embed(["a", "b"])
    assert len(out) == 2
    a_solo = (await embeddings.embed(["a"]))[0]
    b_solo = (await embeddings.embed(["b"]))[0]
    assert out[0] == a_solo
    assert out[1] == b_solo


@pytest.mark.asyncio
async def test_embed_empty_input_returns_empty_list():
    """No inputs → no outputs (no exception)."""
    out = await embeddings.embed([])
    assert out == []


@pytest.mark.asyncio
async def test_embed_query_returns_single_vector():
    """embed_query is the single-string convenience wrapper."""
    vec = await embeddings.embed_query("hello world")
    assert vec is not None
    assert len(vec) == embeddings.EMBEDDING_DIM


@pytest.mark.asyncio
async def test_embed_query_empty_returns_none():
    """Empty query → None (caller falls back to keyword path)."""
    vec = await embeddings.embed_query("")
    assert vec is None


@pytest.mark.asyncio
async def test_embed_different_inputs_produce_different_vectors():
    """Two distinct inputs SHOULD have distinct mock vectors (otherwise
    the mock degenerates to a single representation)."""
    a = await embeddings.embed_query("the user asked about coffee")
    b = await embeddings.embed_query("entirely different content")
    assert a != b


@pytest.mark.asyncio
async def test_embed_real_path_no_key_returns_none_per_item(monkeypatch):
    """When mock is disabled and no API key is present, ``embed`` returns
    a list of None (signal for keyword fallback)."""
    monkeypatch.delenv(embeddings.MOCK_ENV_VAR, raising=False)
    monkeypatch.delenv(embeddings.API_KEY_ENV_VAR, raising=False)
    embeddings.reset_client_cache()
    out = await embeddings.embed(["x", "y"])
    assert out == [None, None]


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(embeddings.API_KEY_ENV_VAR),
    reason=f"{embeddings.API_KEY_ENV_VAR} not set",
)
async def test_embed_real_path_when_key_present(monkeypatch):
    """When the real API key is available, the real path returns
    1536-d vectors. Skipped by default; lights up in CI/local with the key."""
    monkeypatch.delenv(embeddings.MOCK_ENV_VAR, raising=False)
    embeddings.reset_client_cache()
    out = await embeddings.embed(["hello world"])
    assert len(out) == 1
    assert out[0] is not None
    assert len(out[0]) == embeddings.EMBEDDING_DIM
