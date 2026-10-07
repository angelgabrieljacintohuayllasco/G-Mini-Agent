"""Fixtures comunes: los tests nunca tocan la memoria real ni APIs de embeddings."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def hermetic_memory(tmp_path, monkeypatch):
    from backend.core import embeddings, learning, memory_ltm

    monkeypatch.setattr(embeddings, "_embedder", embeddings.EmbeddingProvider(provider="hash"))
    monkeypatch.setattr(memory_ltm, "_ltm", memory_ltm.LongTermMemory(db_path=str(tmp_path / "ltm.db")))
    monkeypatch.setattr(learning, "_learning", None)
    yield
