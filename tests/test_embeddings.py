"""Embeddings y memoria de largo plazo: modelo por vector, circuit breaker, migración."""

from __future__ import annotations

import sqlite3
import time

import numpy as np
import pytest

from backend.core import embeddings
from backend.core.embeddings import HASH_MODEL_ID, Embedding, EmbeddingProvider, HashEmbedder
from backend.core.memory_ltm import LongTermMemory


def _cos(a, b) -> float:
    a, b = np.asarray(a), np.asarray(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def test_hash_embedder_is_deterministic_and_lexical():
    e = HashEmbedder()
    v1 = e.embed("Me llamo Gabriel y vivo en Huancayo")
    assert v1 == e.embed("Me llamo Gabriel y vivo en Huancayo")
    assert len(v1) == embeddings.HASH_DIM and abs(np.linalg.norm(v1) - 1) < 1e-5
    related = _cos(v1, e.embed("¿cómo me llamo? Gabriel"))
    unrelated = _cos(v1, e.embed("La receta del pan lleva harina"))
    assert related > unrelated


class _FlakyBackend:
    model_id = "fake-remote@4"

    def __init__(self, fail: bool):
        self.fail = fail
        self.calls = 0

    def embed_many(self, texts, task):
        self.calls += 1
        if self.fail:
            raise ConnectionError("red caída")
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def _provider_with(backend) -> EmbeddingProvider:
    provider = EmbeddingProvider(provider="hash")
    provider._backend = backend
    return provider


def test_remote_vectors_carry_their_model():
    provider = _provider_with(_FlakyBackend(fail=False))
    emb = provider.embed_text("hola")
    assert emb.model == "fake-remote@4" and emb.dim == 4


def test_failure_falls_back_to_hash_and_cools_down():
    backend = _FlakyBackend(fail=True)
    provider = _provider_with(backend)
    first = provider.embed_text("hola")
    second = provider.embed_text("otra")
    assert first.model == HASH_MODEL_ID and second.model == HASH_MODEL_ID
    assert backend.calls == 1  # durante el enfriamiento no se insiste con la red


def test_local_chat_never_sends_memory_to_cloud(monkeypatch):
    from backend.config import config

    real_get = config.get

    def fake_get(*keys, default=None):
        if keys == ("model_router", "default_provider"):
            return "ollama"
        return real_get(*keys, default=default)

    monkeypatch.setattr(config, "get", fake_get)
    assert isinstance(embeddings._choose_backend("auto", "", 768), HashEmbedder)


@pytest.fixture()
def ltm(tmp_path):
    return LongTermMemory(db_path=str(tmp_path / "ltm.db"))


def test_store_records_model_and_search_only_compares_same_model(ltm):
    ltm.store("El usuario usa Python a diario", "preference")
    foreign = Embedding([1.0, 0.0, 0.0, 0.0], "otro-modelo@4")
    ltm.store("Dato con vector de otro modelo", "fact", embedding=foreign)
    rows = {r["content"]: r for r in ltm.list_memories()}
    assert rows["El usuario usa Python a diario"]["embedding_model"] == HASH_MODEL_ID
    assert rows["Dato con vector de otro modelo"]["embedding_model"] == "otro-modelo@4"
    hits = ltm.search("Python", top_k=5)
    assert [h["content"] for h in hits] == ["El usuario usa Python a diario"]


def test_internal_search_does_not_touch_recency(ltm):
    mid = ltm.store("El usuario prefiere respuestas breves", "preference")
    ltm.search("respuestas breves", touch=False)
    assert ltm.get(mid)["access_count"] == 0
    ltm.search("respuestas breves")
    assert ltm.get(mid)["access_count"] == 1


def test_unrelated_query_returns_nothing(ltm):
    ltm.store("El usuario prefiere respuestas breves", "preference")
    assert ltm.search("receta de pan con levadura") == []


def test_profile_never_calls_embeddings(ltm, monkeypatch):
    ltm.store("El usuario se llama Gabriel", "fact", importance=0.9)
    ltm.store("Detalle menor", "fact", importance=0.2)
    ltm.store("Lección técnica", "learning", importance=1.0)

    def boom(*a, **k):
        raise AssertionError("el perfil no debe usar embeddings")

    monkeypatch.setattr(LongTermMemory, "_embedder", staticmethod(boom))
    top = ltm.top_memories(limit=5)
    assert [m["content"] for m in top] == ["El usuario se llama Gabriel", "Detalle menor"]


def test_migrates_old_schema_and_reembeds(tmp_path):
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE long_term_memory (
            memory_id TEXT PRIMARY KEY, category TEXT NOT NULL, content TEXT NOT NULL, embedding BLOB,
            metadata TEXT NOT NULL DEFAULT '{}', importance REAL NOT NULL DEFAULT 0.5,
            access_count INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, last_accessed REAL NOT NULL,
            expires_at REAL)""")
        now = time.time()
        conn.execute(
            "INSERT INTO long_term_memory VALUES ('old1','fact','El usuario vive en Huancayo',?, '{}', 0.7, 0, ?, ?, NULL)",
            (np.ones(384, dtype=np.float32).tobytes(), now, now),
        )
    ltm = LongTermMemory(db_path=str(db))
    row = ltm.get("old1")
    assert row["status"] == "active" and row["base_importance"] == 0.7
    assert ltm.search("Huancayo") == []  # sin modelo conocido no se compara
    assert [r["memory_id"] for r in ltm.stale_embeddings(HASH_MODEL_ID)] == ["old1"]

    from backend.core.learning import LearningService

    assert LearningService._reembed(ltm) == 1
    assert [h["memory_id"] for h in ltm.search("Huancayo")] == ["old1"]
    LongTermMemory(db_path=str(db))  # reabrir es idempotente
