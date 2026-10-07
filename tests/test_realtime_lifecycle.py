"""Ciclo de vida de Gemini Live contra un servidor local: inicios dobles, cierres y reconexión."""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from backend.voice import realtime
from backend.voice.realtime import RealTimeVoice


class FakeLive:
    """Imita el WebSocket de Gemini Live: responde setupComplete y guarda cada setup."""

    def __init__(self, close_code: int | None = None):
        self.close_code = close_code
        self.setups: list[dict] = []
        self.opened = 0
        self.closed_by_client = 0
        self.port = 0

    async def handler(self, ws):
        self.opened += 1
        first = len(self.setups) == 0
        try:
            self.setups.append(json.loads(await ws.recv())["setup"])
            await ws.send(json.dumps({"setupComplete": {}}))
            if first and self.close_code:
                await asyncio.sleep(0.05)
                await ws.close(code=self.close_code, reason="interno")
                return
            await ws.wait_closed()
            self.closed_by_client += 1
        except websockets.ConnectionClosed:
            self.closed_by_client += 1


@pytest.fixture
async def live(monkeypatch):
    servers = []

    async def start(close_code=None):
        fake = FakeLive(close_code)
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        fake.port = next(iter(server.sockets)).getsockname()[1]
        servers.append(server)

        async def fake_open(self):
            self._ws = await realtime._ws_connect(f"ws://127.0.0.1:{fake.port}")
            return "models/prueba"

        monkeypatch.setattr(RealTimeVoice, "_open_google_socket", fake_open)
        monkeypatch.setattr(RealTimeVoice, "_get_google_system_prompt", lambda self: "prompt")
        return fake

    yield start
    for server in servers:
        server.close()
        await server.wait_closed()


async def _until(predicate, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


async def test_second_start_closes_the_first_session(live):
    fake = await live()
    rv = RealTimeVoice()
    assert await rv.start_session("google")
    first_task = rv._task
    assert await rv.start_session("google")
    assert await _until(lambda: fake.closed_by_client == 1)
    assert fake.opened == 2 and first_task.done() and not rv._task.done()
    await rv.stop_session()


async def test_unexpected_close_tells_the_ui_once(live):
    await live(close_code=1000)
    stopped: list[str] = []

    async def on_stopped(reason):
        stopped.append(reason)

    rv = RealTimeVoice()
    assert await rv.start_session("google", on_stopped=on_stopped)
    assert await _until(lambda: not rv.is_active)
    await asyncio.sleep(0.1)
    assert stopped == ["closed"]


async def test_explicit_stop_is_not_reported_as_a_crash(live):
    await live()
    stopped: list[str] = []

    async def on_stopped(reason):
        stopped.append(reason)

    rv = RealTimeVoice()
    assert await rv.start_session("google", on_stopped=on_stopped)
    await rv.stop_session()
    await asyncio.sleep(0.1)
    assert stopped == [] and not rv.is_active


async def test_reconnect_resumes_on_the_same_backend_with_filtered_tools(live):
    fake = await live(close_code=1011)
    rv = RealTimeVoice()
    rv._mcp_context = ""  # sin MCP, mcp_call_tool no se ofrece
    assert await rv.start_session("google")
    rv._session_resumption_handle = "handle-abc"
    assert await _until(lambda: len(fake.setups) == 2, timeout=8)
    resumed = fake.setups[1]
    names = [d["name"] for d in resumed["tools"][0]["function_declarations"]]
    assert resumed["sessionResumption"] == {"handle": "handle-abc"}
    assert "mcp_call_tool" not in names and {"google_search": {}} in resumed["tools"]
    assert await _until(lambda: rv.is_active and rv._google_ready)
    await rv.stop_session()
