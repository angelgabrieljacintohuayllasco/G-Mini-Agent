"""Contexto de memoria en el prompt: perfil fijo, recuerdo efímero por turno y hooks del agente."""

from __future__ import annotations

import asyncio

from backend.core.memory import Memory
from backend.core.memory_context import (
    PROFILE_HEADER,
    RECALL_HEADER,
    build_profile_context,
    build_recall_context,
)
from backend.core.memory_ltm import LongTermMemory


def test_profile_block_lists_top_memories(tmp_path):
    ltm = LongTermMemory(db_path=str(tmp_path / "ltm.db"))
    mid = ltm.store("El usuario se llama Gabriel", "fact", importance=0.9)
    ltm.store("Lección de trabajo", "learning", importance=1.0)
    block = build_profile_context(ltm)
    assert block.text.startswith(PROFILE_HEADER)
    assert "El usuario se llama Gabriel" in block.text and "Lección" not in block.text
    assert block.ids == {mid}


def test_recall_skips_profile_items_and_never_breaks(tmp_path):
    ltm = LongTermMemory(db_path=str(tmp_path / "ltm.db"))
    in_profile = ltm.store("El usuario programa en Python", "fact", importance=0.9)
    ltm.store("El usuario despliega en Python con Docker", "task", importance=0.4)
    text = build_recall_context("Python", exclude_ids={in_profile}, ltm=ltm)
    assert text.startswith(RECALL_HEADER)
    assert "Docker" in text and "programa en Python" not in text

    class Broken:
        def search(self, *a, **k):
            raise RuntimeError("db bloqueada")

    assert build_recall_context("Python", ltm=Broken()) == ""


def test_turn_context_reaches_the_model_but_not_the_history():
    memory = Memory()
    memory.set_system_prompt("BASE")
    memory.add_user_message("hola [pista]", raw_text="hola")
    memory.set_turn_context("[RECUERDOS] el usuario se llama Gabriel")
    messages = memory.get_llm_messages()
    assert messages[0].content == "BASE\n\n[RECUERDOS] el usuario se llama Gabriel"
    assert all("RECUERDOS" not in m["content"] for m in memory.messages)
    assert memory.messages[0]["raw_text"] == "hola" and memory.messages[0]["origin"] == "user"
    memory.clear_turn_context()
    assert memory.get_llm_messages()[0].content == "BASE"


async def test_agent_reflects_after_turn_and_refreshes_profile(monkeypatch):
    from backend.core import learning
    from backend.core.agent import AgentCore

    agent = AgentCore.__new__(AgentCore)
    agent._memory = Memory()
    agent._memory._messages = [{"role": "user", "content": "me llamo Gabriel", "origin": "user"}]
    agent._memory._session_id = "s1"
    agent._bg_tasks = set()
    applied = []
    agent._apply_system_prompt = lambda: applied.append(True)

    class _Learning:
        async def reflect_on_turn(self, convo, *, session_id=""):
            assert convo[0]["content"] == "me llamo Gabriel" and session_id == "s1"
            return 1

    monkeypatch.setattr(learning, "get_learning", lambda: _Learning())
    agent._schedule_reflection()
    await asyncio.gather(*list(agent._bg_tasks))
    assert applied == [True]
