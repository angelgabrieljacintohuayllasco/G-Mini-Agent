"""Asistente inicial, identidad del agente y acciones de memoria."""

from __future__ import annotations

import sqlite3

import pytest

from backend.core import identity, onboarding
from backend.core.onboarding import OnboardingService


class _Store:
    """Config falsa: registra exactamente qué se escribió."""

    def __init__(self, initial: dict | None = None):
        self.data = dict(initial or {})
        self.writes: dict[tuple, object] = {}
        self.keys: dict[str, str] = {}

    def get(self, *keys, default=None):
        return self.data.get(keys, default)

    def set(self, *keys, value):
        self.data[keys] = value
        self.writes[keys] = value

    def set_api_key(self, vault_name, api_key):  # misma firma que Config.set_api_key
        self.keys[vault_name] = api_key


def _service(store: _Store, ready: bool = True) -> OnboardingService:
    return OnboardingService(
        getter=store.get, setter=store.set, key_setter=store.set_api_key,
        on_provider_saved=lambda provider: ready,
    )


def _defaults(step: dict) -> dict:
    return {f["name"]: f.get("default") for f in step["fields"]}


EXISTING = {
    ("model_router", "default_provider"): "vertex",
    ("model_router", "default_model"): "gemini-3.8-flash",
    ("agent", "autonomy"): "media",
    ("agent", "autonomy_level"): "supervisado",
}


def test_accepting_every_default_only_marks_completed():
    store = _Store(EXISTING)
    svc = _service(store)
    step = svc.start()
    while step.get("status") == "running":
        step = svc.answer(step["id"], _defaults(step))
    assert step == {"status": "done"}
    assert store.writes == {("onboarding", "completed"): True}


def test_full_setup_persists_exactly_what_the_user_chose():
    store = _Store(EXISTING)
    svc = _service(store)
    answers = {
        "welcome": {"language": "en"},
        "identity": {"agent_name": "Nova", "personality": "Directo, técnico y sin rodeos.", "user_name": "Gabriel"},
        "provider": {"provider": "openai", "api_key": "sk-test-1234567890"},
        "model": {"model": "gpt-6.1-sol"},
        "autonomy": {"autonomy": "alta", "permission": "libre"},
        "voice": {"voice": True},
        "profile_optin": {"profile_build": False},
    }
    step = svc.start()
    while step.get("status") == "running":
        step = svc.answer(step["id"], answers[step["id"]])
    assert store.keys == {"openai_api": "sk-test-1234567890"}
    assert store.writes == {
        ("app", "language"): "en",
        ("agent", "name"): "Nova",
        ("agent", "soul"): "Directo, técnico y sin rodeos.",
        ("model_router", "default_provider"): "openai",
        ("model_router", "default_model"): "gpt-6.1-sol",
        ("agent", "autonomy"): "alta",
        ("agent", "autonomy_level"): "libre",
        ("voice", "auto_tts"): True,
        ("voice", "enabled"): True,
        ("onboarding", "profile_build"): "off",
        ("onboarding", "completed"): True,
    }
    from backend.core.memory_ltm import get_ltm

    assert [m["content"] for m in get_ltm().list_memories()] == ["El usuario se llama Gabriel."]


def test_model_options_follow_the_chosen_provider():
    store = _Store(EXISTING)
    svc = _service(store)
    svc.start()
    svc.answer("welcome", {"language": "es"})
    svc.answer("identity", {"agent_name": "G-Mini"})
    step = svc.answer("provider", {"provider": "anthropic"})
    assert step["id"] == "model"
    assert [o["value"] for o in step["fields"][0]["options"]][0] == "claude-opus-5-5"


def test_invalid_answer_keeps_the_step_with_an_error():
    svc = _service(_Store(EXISTING))
    svc.start()
    svc.answer("welcome", {"language": "es"})
    step = svc.answer("identity", {"agent_name": "<script>"})
    assert step["id"] == "identity" and step["notice"]["kind"] == "error"


def test_missing_credentials_warn_but_continue():
    svc = _service(_Store(EXISTING), ready=False)
    svc.start()
    svc.answer("welcome", {})
    svc.answer("identity", {"agent_name": "G-Mini"})
    step = svc.answer("provider", {"provider": "vertex"})
    assert step["id"] == "model" and step["notice"]["kind"] == "warning"
    assert "gcloud" in step["notice"]["text"]


def test_double_submit_and_back_are_safe():
    svc = _service(_Store(EXISTING))
    svc.start()
    second = svc.answer("welcome", {"language": "es"})
    again = svc.answer("welcome", {"language": "es"})  # doble clic
    assert second["id"] == again["id"] == "identity"
    assert svc.back()["id"] == "welcome"


def test_completed_install_does_not_restart_unless_asked():
    store = _Store({**EXISTING, ("onboarding", "completed"): True})
    svc = _service(store)
    assert svc.start()["status"] == "done"
    assert svc.start(rerun=True)["id"] == "welcome"


def test_cancel_stops_offering_the_wizard():
    store = _Store(EXISTING)
    svc = _service(store)
    svc.start()
    assert svc.cancel() == {"status": "cancelled"}
    assert onboarding.is_first_run(store.get) and not onboarding.should_offer(store.get)


def test_existing_install_is_migrated(tmp_path, monkeypatch):
    monkeypatch.setattr(onboarding.config, "get_api_key", staticmethod(lambda vault: None))
    store = _Store()
    assert onboarding.migrate_existing_install(store.get, store.set, data_dir=tmp_path) is False
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        conn.execute("CREATE TABLE sessions (session_id TEXT, message_count INTEGER)")
        conn.execute("INSERT INTO sessions VALUES ('s1', 4)")
    assert onboarding.migrate_existing_install(store.get, store.set, data_dir=tmp_path) is True
    assert store.writes == {("onboarding", "completed"): True}


# ── Identidad ───────────────────────────────────────────────────────────

def test_identity_block_reflects_name_personality_and_language():
    store = _Store({("agent", "name"): "Nova", ("agent", "soul"): "Cercano", ("app", "language"): "en"})
    block = identity.build_identity_context(store.get)
    assert "Te llamas Nova" in block and "Cercano" in block and "inglés" in block
    assert identity.build_identity_context(_Store().get) == ""


@pytest.mark.parametrize("bad", ["", "   ", "<script>", "x" * 40, "12345"])
def test_invalid_names_are_rejected(bad):
    with pytest.raises(ValueError):
        identity.validate_name(bad)


# ── Acciones de memoria ─────────────────────────────────────────────────

def test_memory_search_and_forget():
    from backend.core import memory_actions
    from backend.core.memory_ltm import get_ltm

    mid = get_ltm().store("El usuario trabaja en Acme", "fact")
    found = memory_actions.run("memory_search", {"query": "Acme"})
    assert found["success"] and found["data"]["memories"][0]["memory_id"] == mid
    gone = memory_actions.run("memory_forget", {"memory_id": mid})
    assert gone["success"] and get_ltm().get(mid) is None
    assert not memory_actions.run("memory_forget", {"memory_id": mid})["success"]


def test_agent_rename_validates_and_saves(monkeypatch):
    from backend.core import memory_actions

    saved = {}
    monkeypatch.setattr(identity.config, "set", lambda *k, value: saved.update({k: value}))
    assert memory_actions.run("agent_rename", {"name": "Nova"})["success"]
    assert saved == {("agent", "name"): "Nova"}
    assert not memory_actions.run("agent_rename", {"name": "<b>"})["success"]


def test_memory_actions_policy():
    from backend.core.planner import Action
    from backend.core.policy import PolicyEngine

    engine = PolicyEngine.__new__(PolicyEngine)
    assert engine._classify(Action(type="memory_search", params={}))["severity"] == "low"
    assert engine._classify(Action(type="memory_forget", params={}))["severity"] == "medium"
    assert engine._classify(Action(type="agent_rename", params={}))["severity"] == "low"


def test_profile_directive_only_once_and_only_with_consent(monkeypatch):
    from backend.core.agent import AgentCore

    store = _Store({("onboarding", "profile_build"): "ask"})
    monkeypatch.setattr(onboarding.config, "get", store.get)
    monkeypatch.setattr(onboarding.config, "set", store.set)
    agent = AgentCore.__new__(AgentCore)
    assert "perfil" in agent._profile_directive_for_turn()
    onboarding.mark_seen(flag="profile_build_offered")
    assert agent._profile_directive_for_turn() == ""
    store.data = {("onboarding", "profile_build"): "off"}
    assert agent._profile_directive_for_turn() == ""


async def test_socket_errors_reach_the_ui(monkeypatch):
    from backend.api import websocket_handler

    emitted = []

    async def fake_emit(event, data=None, to=None):
        emitted.append((event, data))

    class Broken:
        def answer(self, *a, **k):
            raise RuntimeError("keyring no disponible")

    monkeypatch.setattr(websocket_handler.sio, "emit", fake_emit)
    monkeypatch.setattr(onboarding, "get_onboarding", lambda: Broken())
    await websocket_handler.onboarding_answer_ws("sid1", {"step_id": "provider", "value": {}})
    assert emitted[0][0] == "onboarding:error" and "keyring" in emitted[0][1]["message"]
