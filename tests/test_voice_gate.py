"""La voz (nativa y simulada) pasa por la misma policy que el chat."""

from __future__ import annotations

import pytest

from backend.core import agent as agent_mod
from backend.core.agent import AgentCore
from backend.core.memory import Memory
from backend.core.planner import Action


class _Policy:
    def __init__(self, review):
        self.review = review
        self.calls = 0

    def review_actions(self, actions, mode_key=None):
        self.calls += 1
        return dict(self.review)


@pytest.fixture()
def agent(monkeypatch):
    sent = {"messages": [], "approvals": []}

    async def fake_message(sid, text, kind="system", done=False):
        sent["messages"].append((kind, text))

    async def fake_approval(sid, pending, **kwargs):
        sent["approvals"].append(pending)

    monkeypatch.setattr(agent_mod, "emit_message", fake_message)
    monkeypatch.setattr(agent_mod, "emit_approval_state", fake_approval)
    core = AgentCore.__new__(AgentCore)
    core._memory = Memory()
    core._current_mode = "normal"
    core._pending_approval = None
    core.sent = sent
    return core


async def test_allowed_actions_pass(agent):
    agent._policy = _Policy({"blocked": False, "requires_approval": False, "findings": []})
    actions = [Action(type="browser_extract", params={})]
    allowed, refused = await agent._gate_voice_actions("sid", actions)
    assert allowed == actions and refused == []


async def test_blocked_actions_never_run(agent):
    agent._policy = _Policy({"blocked": True, "findings": [{"action": "terminal_run", "effect": "deny",
                                                            "reason": "el modo tutor no habilita la terminal"}]})
    allowed, refused = await agent._gate_voice_actions("sid", [Action(type="terminal_run", params={"command": "del x"})])
    assert allowed == [] and "tutor" in refused[0]["message"]
    assert agent.sent["messages"][0][0] == "warning"


async def test_sensitive_actions_wait_for_approval(agent):
    agent._policy = _Policy({"blocked": False, "requires_approval": True, "findings": [
        {"action": "terminal_run", "reason": "ejecuta comandos"}]})
    action = Action(type="terminal_run", params={"command": "pip install x"})
    allowed, refused = await agent._gate_voice_actions("sid", [action])
    assert allowed == [] and "aprobación" in refused[0]["message"]
    assert agent._pending_approval["source"] == "voice" and agent._pending_approval["actions"] == [action]
    assert agent.sent["approvals"] == [True]
    again_allowed, again = await agent._gate_voice_actions("sid", [action])
    assert again_allowed == [] and "esperando" in again[0]["message"]


async def test_native_voice_refusal_message(agent):
    agent._policy = _Policy({"blocked": True, "findings": [{"action": "x", "effect": "deny", "reason": "no"}]})
    assert "Bloqueada" in await agent._voice_refusal("sid", "terminal_run", {"command": "x"})
    agent._policy = _Policy({"blocked": False, "requires_approval": False, "findings": []})
    assert await agent._voice_refusal("sid", "browser_extract", {}) is None


async def test_simulated_voice_respects_the_gate(monkeypatch):
    from backend.voice.simulated_realtime import SimulatedRealtimeVoice

    executed = []

    class _Planner:
        def parse_actions(self, text):
            return [Action(type="terminal_run", params={"command": "format c:"})]

        async def execute_actions(self, actions):
            executed.extend(actions)
            return [{"action": a.type, "success": True} for a in actions]

    class _Sio:
        async def emit(self, *a, **k):
            pass

    gated = []

    async def deny_all(actions):
        gated.extend(actions)
        return [], [{"action": a.type, "success": False, "message": "no"} for a in actions]

    voice = SimulatedRealtimeVoice.__new__(SimulatedRealtimeVoice)
    voice._planner, voice._sio, voice._sid, voice._memory = _Planner(), _Sio(), "sid", None
    voice._action_gate = deny_all
    try:
        await voice._execute_llm_actions("[ACTION:terminal_run(command=\"format c:\")]")
    except Exception:
        pass  # el resto del turno (segundo LLM) no está configurado en esta prueba
    assert [a.type for a in gated] == ["terminal_run"] and executed == []


def test_subagents_cannot_run_sensitive_actions():
    from backend.core.subagents import SubAgentOrchestrator

    orchestrator = SubAgentOrchestrator()
    orchestrator._policy = _Policy({"blocked": False, "requires_approval": True, "findings": []})
    actions = [Action(type="terminal_run", params={"command": "rm -rf x"})]
    allowed, refused = orchestrator._gate_actions(actions, "normal")
    assert allowed == [] and "coordinador" in refused[0]["message"]
    orchestrator._policy = _Policy({"blocked": False, "requires_approval": False, "findings": []})
    assert orchestrator._gate_actions(actions, "normal") == (actions, [])
