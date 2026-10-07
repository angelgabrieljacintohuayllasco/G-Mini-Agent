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


@pytest.fixture(autouse=True)
def hermetic_agent_skills(tmp_path, monkeypatch):
    """Las skills SKILL.md de los tests viven en una carpeta temporal."""
    from backend.core import agent_skills

    home = tmp_path / "agent_skills"
    for attr, value in (("SKILLS_DIR", home), ("BUNDLED_DIR", home / "bundled"),
                        ("INSTALLED_DIR", home / "installed"), ("AGENT_DIR", home / "agent"),
                        ("STATE_FILE", home / "state.json")):
        monkeypatch.setattr(agent_skills, attr, value)
    yield home
