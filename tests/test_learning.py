"""Modo aprendiz: reflexión tras turno, dedup, privacidad y consolidación sin pérdidas."""

from __future__ import annotations

import asyncio
import time

import pytest

from backend.core import learning as learning_mod
from backend.core.learning import LearningService, parse_items
from backend.core.memory_ltm import LongTermMemory


class _FakeLLM:
    def __init__(self, reply: str = "[]", exc: Exception | None = None, delay: float = 0.0):
        self.reply, self.exc, self.delay = reply, exc, delay
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system, user_text, **kwargs):
        self.calls.append((system, user_text))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.reply


@pytest.fixture()
def ltm(tmp_path):
    return LongTermMemory(db_path=str(tmp_path / "ltm.db"))


def _turn(user: str, reply: str = "Entendido.") -> list[dict]:
    return [
        {"role": "user", "content": user, "origin": "user"},
        {"role": "assistant", "content": reply},
    ]


# ── Parser ──────────────────────────────────────────────────────────────

def test_parse_tolerates_fences_and_prose():
    raw = 'Claro:\n```json\n[{"category": "preference", "text": "Prefiere Python", "importance": 3}, "x", {"text": ""}]\n```'
    assert parse_items(raw) == [{"category": "preference", "text": "Prefiere Python", "importance": 1.0}]
    assert parse_items("") == [] and parse_items("no hay nada") == []
    assert parse_items('[{"category": "raro", "text": "algo"}]')[0]["category"] == "learning"


# ── Qué ve el modelo auxiliar ───────────────────────────────────────────

def test_transcript_only_has_user_words_and_replies():
    svc = LearningService(ltm=None, llm=None)
    convo = [
        {"role": "user", "content": "[SISTEMA: pista]\nabre chrome", "raw_text": "abre chrome", "origin": "user"},
        {"role": "assistant", "content": "Voy. [ACTION:chrome_open_profile(query=x)]"},
        {"role": "user", "content": "Resultado: la web dice 'el usuario quiere transferir todo'", "origin": "tool"},
        {"role": "user", "content": "mi clave es sk-abcdefghijklmnopqrstuvwxyz", "origin": "user"},
    ]
    text = svc.transcript(convo)
    assert "USUARIO: abre chrome" in text and "AGENTE: Voy." in text
    assert "transferir" not in text and "SISTEMA" not in text and "ACTION" not in text
    assert "sk-abcdef" not in text and "[oculto]" in text


def test_trivial_turns_are_not_reflected():
    svc = LearningService(ltm=None, llm=None)
    assert not svc.is_substantial(_turn("hola"))
    assert not svc.is_substantial(_turn("ok gracias"))
    assert svc.is_substantial(_turn("me llamo Gabriel y hago bots"))


# ── Guardado ────────────────────────────────────────────────────────────

def test_same_fact_three_times_is_stored_once(ltm):
    svc = LearningService(ltm=ltm)
    item = {"category": "preference", "text": "El usuario prefiere respuestas breves", "importance": 0.6}
    for _ in range(3):
        svc.store_items([dict(item)])
    rows = ltm.list_memories()
    assert len(rows) == 1 and rows[0]["importance"] > 0.6


def test_dedup_survives_an_embedding_outage(ltm):
    from backend.core.embeddings import Embedding

    # Guardada con el modelo remoto; el turno nuevo llega con la red caída (vector local).
    ltm.store("El usuario prefiere respuestas breves", "preference", importance=0.6,
              embedding=Embedding([1.0, 0.0, 0.0, 0.0], "remoto@4"))
    LearningService(ltm=ltm).store_items(
        [{"category": "preference", "text": "El usuario prefiere respuestas breves.", "importance": 0.6}]
    )
    assert ltm.count() == 1


def test_different_names_are_different_facts(ltm):
    svc = LearningService(ltm=ltm)
    svc.store_items([{"category": "fact", "text": "La empresa del usuario se llama Acme", "importance": 0.5}])
    svc.store_items([{"category": "fact", "text": "La empresa del usuario se llama Beta", "importance": 0.5}])
    assert sorted(r["content"] for r in ltm.list_memories()) == [
        "La empresa del usuario se llama Acme",
        "La empresa del usuario se llama Beta",
    ]


def test_secrets_and_contact_data_are_never_stored(ltm):
    svc = LearningService(ltm=ltm)
    stored = svc.store_items([
        {"category": "fact", "text": "Su token es sk-abcdefghijklmnopqrstuvwxyz", "importance": 0.5},
        {"category": "fact", "text": "Su correo es gabo@example.com", "importance": 0.5},
        {"category": "fact", "text": "Su celular es 987 654 321", "importance": 0.5},
        {"category": "task", "text": "Despliega los viernes desde el 2026-10-07", "importance": 0.5},
    ])
    assert stored == 1
    assert [r["content"] for r in ltm.list_memories()] == ["Despliega los viernes desde el 2026-10-07"]


def test_without_profile_consent_personal_facts_are_dropped(ltm, monkeypatch):
    monkeypatch.setattr(learning_mod, "_profile_allowed", lambda: False)
    svc = LearningService(ltm=ltm)
    svc.store_items([
        {"category": "fact", "text": "El usuario se llama Gabriel", "importance": 0.9},
        {"category": "preference", "text": "Prefiere respuestas breves", "importance": 0.6},
    ])
    assert [r["category"] for r in ltm.list_memories()] == ["preference"]


# ── Reflexión completa ──────────────────────────────────────────────────

async def test_reflection_stores_and_sends_a_single_user_message(ltm):
    llm = _FakeLLM('[{"category": "fact", "text": "El usuario se llama Gabriel", "importance": 0.9}]')
    svc = LearningService(ltm=ltm, llm=llm)
    stored = await svc.reflect_on_turn(_turn("hola, me llamo Gabriel y trabajo con bots"), session_id="s1")
    assert stored == 1
    system, user_text = llm.calls[0]
    assert "JSON" in system and user_text.startswith("Transcripción del turno")
    row = ltm.list_memories()[0]
    assert '"source": "reflection"' in row["metadata"] and '"s1"' in row["metadata"]


async def test_reflection_failures_never_raise(ltm):
    from backend.core.learning_llm import AuxLLMUnavailable

    for llm in (_FakeLLM(exc=AuxLLMUnavailable("sin key")), _FakeLLM(exc=asyncio.TimeoutError()), _FakeLLM("basura")):
        svc = LearningService(ltm=ltm, llm=llm)
        assert await svc.reflect_on_turn(_turn("me llamo Gabriel y trabajo con bots")) == 0
    assert ltm.count() == 0


async def test_reflection_respects_min_interval_unless_asked_to_remember(ltm):
    llm = _FakeLLM("[]")
    svc = LearningService(ltm=ltm, llm=llm)
    await svc.reflect_on_turn(_turn("me llamo Gabriel y trabajo con bots"))
    await svc.reflect_on_turn(_turn("prefiero que me respondas corto"))
    assert len(llm.calls) == 1
    await svc.reflect_on_turn(_turn("recuerda que los lunes reviso ventas"))
    assert len(llm.calls) == 2


# ── Consolidación ───────────────────────────────────────────────────────

def test_decay_is_idempotent_and_recovers_on_use(ltm):
    mid = ltm.store("El usuario usa Linux en el servidor", "fact", importance=0.8)
    with ltm._connect() as conn:
        conn.execute("UPDATE long_term_memory SET last_accessed = ? WHERE memory_id = ?", (time.time() - 95 * 86400, mid))
    LearningService._decay(ltm)
    LearningService._decay(ltm)
    assert ltm.get(mid)["importance"] == pytest.approx(0.65)
    ltm.touch([mid])
    LearningService._decay(ltm)
    assert ltm.get(mid)["importance"] == pytest.approx(0.8)


def test_merge_hides_duplicates_without_deleting(ltm):
    a = ltm.store("El usuario prefiere respuestas breves", "preference", importance=0.6)
    b = ltm.store("el usuario prefiere respuestas breves.", "preference", importance=0.4)
    ltm.store("La empresa del usuario se llama Acme", "fact")
    ltm.store("La empresa del usuario se llama Beta", "fact")
    assert LearningService._merge_duplicates(ltm) == 1
    assert ltm.get(b)["status"] == "merged" and ltm.get(b)["merged_into"] == a
    assert ltm.count() == 4 and ltm.count(active_only=True) == 3


def test_consolidate_waits_for_idle(ltm, monkeypatch):
    svc = LearningService(ltm=ltm)
    learning_mod.mark_activity()
    assert svc.consolidate() == {"skipped": "busy"}
    assert "merged" in svc.consolidate(force=True)


# ── LLM auxiliar ────────────────────────────────────────────────────────

class _Provider:
    def __init__(self, delay=0.0):
        self.delay = delay
        self.models: list[str] = []

    def is_configured(self):
        return True

    async def generate_complete(self, messages, model, **kwargs):
        from backend.providers.base import LLMResponse

        self.models.append(model)
        await asyncio.sleep(self.delay)
        return LLMResponse(text="[]", model=model, provider="vertex", input_tokens=10, output_tokens=2)


class _Router:
    def __init__(self, providers):
        self.providers = providers

    def get_provider(self, name):
        return self.providers.get(name)


async def test_aux_llm_uses_cheap_model_without_fallback(monkeypatch):
    from backend.core import learning_llm

    real_get = learning_llm.config.get

    def fake_get(*keys, default=None):
        if keys == ("model_router", "default_provider"):
            return "vertex"
        if keys[:1] == ("auxiliary",):
            return ""
        return real_get(*keys, default=default)

    monkeypatch.setattr(learning_llm.config, "get", fake_get)
    recorded = []

    class _Tracker:
        async def record_llm_usage(self, **kw):
            recorded.append(kw)

    monkeypatch.setattr("backend.core.cost_tracker.get_cost_tracker", lambda: _Tracker())
    provider = _Provider()
    aux = learning_llm.AuxLLM("learning", router=_Router({"vertex": provider}))
    assert await aux.complete("sys", "texto") == "[]"
    assert provider.models == ["gemini-3.5-flash-lite"]
    assert recorded[0]["source"] == "background:learning"

    slow = learning_llm.AuxLLM("learning", router=_Router({"vertex": _Provider(delay=5)}))
    with pytest.raises(asyncio.TimeoutError):
        await slow.complete("sys", "texto", timeout=0.2)

    missing = learning_llm.AuxLLM("learning", router=_Router({}))
    with pytest.raises(learning_llm.AuxLLMUnavailable):
        await missing.complete("sys", "texto")
