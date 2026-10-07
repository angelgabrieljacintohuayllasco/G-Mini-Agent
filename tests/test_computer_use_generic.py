"""Computer use en modo genérico: cualquier modelo con visión controla el escritorio con acciones JSON."""

from __future__ import annotations

import base64
import itertools
from types import SimpleNamespace

import pytest

from backend.core import computer_use_agent as cu
from backend.core.computer_use_agent import ComputerUseAgent, _parse_json_action


class FakeAutomation:
    def __init__(self):
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        async def record(*args, **kwargs):
            self.calls.append((name, args))
            return True
        return record


class FakeVision:
    def __init__(self):
        self.counter = itertools.count()

    async def analyze_screen(self, mode="computer_use", monitor=0):
        frame = f"frame-{next(self.counter)}".encode()  # cada captura distinta: sin estancamiento
        return {"image_base64": base64.b64encode(frame).decode(), "screen_dimensions": {}}


class FakeRouter:
    def __init__(self, answers):
        self.answers = list(answers)
        self.seen: list = []

    def get_current_provider_name(self):
        return "vertex"

    def get_current_model(self):
        return "gemini-3.8-flash"

    async def generate_complete(self, messages, model=None, provider_name=None, **kwargs):
        self.seen.append(messages)
        return SimpleNamespace(text=self.answers.pop(0), input_tokens=10, output_tokens=5,
                               provider="vertex", model="gemini-3.8-flash")


@pytest.fixture
def setup(monkeypatch):
    real_get = cu.config.get
    overrides = {("computer_use", "stabilization_delay_seconds"): 0, ("computer_use", "provider"): "generic"}

    def fake_get(*keys, default=None):
        return overrides.get(keys, real_get(*keys, default=default))

    monkeypatch.setattr(cu.config, "get", fake_get)
    monkeypatch.setattr(cu, "_get_monitor_info", lambda monitor: {"left": 0, "top": 0, "width": 1000, "height": 500})
    return overrides


def test_parse_json_action():
    assert _parse_json_action('```json\n{"action": "click", "x": 5}\n```') == {"action": "click", "x": 5}
    assert _parse_json_action('Voy a hacer click. {"action": "key", "keys": "win"} listo') == {"action": "key", "keys": "win"}
    assert _parse_json_action("sin json") is None
    assert _parse_json_action('{"roto": ') is None


async def test_generic_loop_clicks_types_and_finishes(setup):
    router = FakeRouter([
        '{"thought": "veo el campo", "action": "click", "x": 500, "y": 500}',
        '{"action": "type", "x": 100, "y": 200, "text": "hola", "enter": true}',
        '{"action": "done", "summary": "Escribí hola"}',
    ])
    auto = FakeAutomation()
    agent = ComputerUseAgent(FakeVision(), auto, router=router)
    result = await agent.execute_task("escribe hola", max_iterations=5, timeout_seconds=30)

    assert result.status == "completed" and result.summary == "Escribí hola"
    assert result.provider == "generic" and result.model == "vertex:gemini-3.8-flash"
    clicks = [args for name, args in auto.calls if name == "click"]
    assert clicks[0] == (500, 250) and clicks[1] == (100, 100)  # 0-1000 -> píxeles del monitor
    assert ("type_text", ("hola",)) in auto.calls and ("press_key", ("enter",)) in auto.calls
    first_user = router.seen[0][1]
    assert first_user.images and "escribe hola" in first_user.content


async def test_bad_answers_end_in_a_clear_failure(setup):
    router = FakeRouter(["no sé", "tampoco", "nada"])
    agent = ComputerUseAgent(FakeVision(), FakeAutomation(), router=router)
    result = await agent.execute_task("algo", max_iterations=5, timeout_seconds=30)
    assert result.status == "failed" and "formato" in result.error


async def test_google_without_key_falls_back_to_generic(setup, monkeypatch):
    setup[("computer_use", "provider")] = "google"

    def no_key(self):
        raise RuntimeError("API key de Google no configurada (vault: google_api).")

    monkeypatch.setattr(ComputerUseAgent, "_init_google", no_key)
    router = FakeRouter(['{"action": "done", "summary": "ok"}'])
    agent = ComputerUseAgent(FakeVision(), FakeAutomation(), router=router)
    result = await agent.execute_task("algo", max_iterations=3, timeout_seconds=30)
    assert result.status == "completed" and result.provider == "generic"


async def test_native_model_missing_in_the_project_switches_to_generic(setup, monkeypatch):
    setup[("computer_use", "provider")] = "google"

    class MissingModel:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                raise RuntimeError("404 NOT_FOUND. Publisher model ... was not found")

    monkeypatch.setattr(ComputerUseAgent, "_init_google", lambda self: MissingModel())
    router = FakeRouter(['{"action": "done", "summary": "hecho con el modelo de chat"}'])
    agent = ComputerUseAgent(FakeVision(), FakeAutomation(), router=router)
    result = await agent.execute_task("algo", max_iterations=3, timeout_seconds=30)
    assert result.status == "completed" and result.provider == "generic"
    assert agent._provider == "generic"
