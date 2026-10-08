"""Fixtures comunes: los tests nunca tocan la memoria real ni APIs de embeddings."""

from __future__ import annotations

import importlib
import tempfile
from pathlib import Path

import pytest

# Los tests nunca escriben los datos de quien desarrolla: config.user.yaml y las
# bases de data/ (gateway, event bus, scheduler, costos...) van a una carpeta
# temporal. Antes, enviar un webhook de prueba dejaba un secreto en la config real.
_TEST_HOME = Path(tempfile.mkdtemp(prefix="gmini-tests-"))


def _isolate_user_data() -> None:
    from backend import config as config_module

    config_module.USER_CONFIG = _TEST_HOME / "config.user.yaml"

    def redirect(node):
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if isinstance(value, str) and key.endswith("db_path") and value.endswith(".db"):
                node[key] = str(_TEST_HOME / Path(value).name)
            else:
                redirect(value)

    redirect(config_module.config._data)
    for module_name, attr in (
        ("backend.core.gateway_service", "DEFAULT_DB_PATH"),
        ("backend.core.node_manager", "DEFAULT_DB_PATH"),
        ("backend.core.smart_home_manager", "DEFAULT_DB_PATH"),
        ("backend.core.scheduler", "DEFAULT_DB_PATH"),
        ("backend.core.canvas", "DEFAULT_DB_PATH"),
        ("backend.core.cost_tracker", "DEFAULT_BUDGET_DB_PATH"),
        ("backend.core.memory", "DB_PATH"),
    ):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        current = getattr(module, attr, None)
        if current is not None:
            setattr(module, attr, _TEST_HOME / Path(str(current)).name)


_isolate_user_data()


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
