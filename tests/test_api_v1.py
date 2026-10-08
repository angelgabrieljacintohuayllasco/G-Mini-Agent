"""API remota v1: chat (JSON, SSE y WebSocket), emparejamiento, scopes, voz y tareas."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.api import websocket_handler
from backend.core.memory import Memory
from backend.security import local_auth

TOKEN = "s" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}
LOCAL_HOST = {"Host": "127.0.0.1:8765"}  # el TestClient usa "testserver" en WebSockets


class FakeVoice:
    tts_is_browser = False
    tts_available = True

    async def synthesize(self, text, voice_id=None):
        return b"RIFF" + text.encode()

    async def transcribe(self, audio):
        return "qué hora es" if audio else ""


class FakeAgent:
    """Emite por sio.emit como el agente real: así se prueba el envoltorio de eventos."""

    def __init__(self):
        self._session_context_lock = asyncio.Lock()
        self._pending_approval = None
        self.memory = Memory()
        self.voice = FakeVoice()
        self.prompts: list[str] = []
        self.stopped = False

    async def process_message(self, sid, text, attachments=None):
        self.prompts.append(text)
        sio = websocket_handler.sio
        async with self._session_context_lock:
            await sio.emit("agent:status", {"status": "thinking"}, to=sid)
            await sio.emit("agent:message", {"text": "Hola ", "type": "text", "done": False}, to=sid)
            await sio.emit("agent:action", {"actionId": "a1", "type": "connector_call", "params": {"x": 1}}, to=sid)
            await sio.emit("agent:action_result", {"actionId": "a1", "type": "connector_call", "success": True,
                                                    "result": "ok"}, to=sid)
            await sio.emit("agent:message", {"text": "Gabriel", "type": "text", "done": False}, to=sid)
            await sio.emit("agent:message", {"text": "", "type": "text", "done": True}, to=sid)
            await sio.emit("agent:status", {"status": "idle"}, to=sid)

    async def stop(self):
        self.stopped = True


@pytest.fixture()
def agent(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(local_auth, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(local_auth, "SESSION_TOKEN_FILE", runtime / "session_token")
    monkeypatch.setattr(local_auth, "DEVICES_FILE", runtime / "devices.json")
    monkeypatch.setenv("GMINI_SESSION_TOKEN", TOKEN)
    monkeypatch.delenv("GMINI_BIND_HOST", raising=False)
    local_auth.reset_session_token_for_tests()
    local_auth._pairing_codes.clear()
    local_auth._claim_attempts.clear()
    from backend.core import remote_tasks

    monkeypatch.setattr(remote_tasks, "DB_PATH", tmp_path / "remote_tasks.db")
    monkeypatch.setattr(remote_tasks, "_initialized", False)
    fake = FakeAgent()
    monkeypatch.setattr(websocket_handler, "_agent_core", fake)
    yield fake
    local_auth.reset_session_token_for_tests()


@pytest.fixture()
def client(agent):
    from backend.main import create_app

    return TestClient(create_app(), base_url="http://127.0.0.1:8765")


def test_health_is_public_and_the_rest_needs_a_token(client):
    health = client.get("/api/v1/health")
    assert health.status_code == 200 and health.json()["protocol"] == 1
    assert client.get("/api/v1/me").status_code == 401
    me = client.get("/api/v1/me", headers=AUTH).json()
    assert me["kind"] == "session" and "admin" in me["scopes"]


def test_chat_collects_reply_and_actions(client, agent):
    resp = client.post("/api/v1/chat", headers=AUTH, json={"message": "hola"})
    data = resp.json()
    assert resp.status_code == 200 and data["reply"] == "Hola Gabriel"
    assert data["actions"] == [{"action": "connector_call", "params": {"x": 1}, "success": True, "message": "ok"}]
    assert agent.prompts == ["hola"]


def test_chat_streams_server_sent_events(client):
    with client.stream("POST", "/api/v1/chat", headers=AUTH, json={"message": "hola", "stream": True}) as resp:
        body = "".join(resp.iter_text())
    events = [line.split(": ", 1)[1] for line in body.splitlines() if line.startswith("event: ")]
    assert events[0] == "start" and events[-1] == "done"
    assert events.count("chunk") == 2 and "action" in events and "state" in events
    done = json.loads(body.strip().split("data: ")[-1])
    assert done["reply"] == "Hola Gabriel"


def test_busy_agent_answers_409(client, agent):
    async def hold():
        await agent._session_context_lock.acquire()

    asyncio.run(hold())  # el lock queda tomado
    resp = client.post("/api/v1/chat", headers=AUTH, json={"message": "hola"})
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "busy"


def test_pairing_scopes_and_revocation(client):
    code = client.post("/api/v1/pairing", headers=AUTH, json={"label": "Carita", "device_type": "esp32"}).json()
    assert len(code["code"]) == 6 and code["qr_payload"].startswith("gmini://pair?")
    claim = client.post("/api/v1/pairing/claim", json={"code": code["code"], "device_name": "Sala"}).json()
    device = {"Authorization": f"Bearer {claim['token']}"}
    me = client.get("/api/v1/me", headers=device).json()
    assert me["kind"] == "device" and "admin" not in me["scopes"]
    forbidden = client.get("/api/v1/devices", headers=device)
    assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "missing_scope"
    assert client.delete(f"/api/v1/devices/{claim['device_id']}", headers=AUTH).status_code == 200
    assert client.get("/api/v1/me", headers=device).status_code == 401
    reused = client.post("/api/v1/pairing/claim", json={"code": code["code"], "device_name": "Otra"})
    assert reused.status_code == 401


def test_voice_endpoints(client):
    tts = client.post("/api/v1/voice/tts", headers=AUTH, json={"text": "hola"})
    assert tts.headers["content-type"] == "audio/wav" and tts.content == b"RIFFhola"
    stt = client.post("/api/v1/voice/stt", headers=AUTH, content=b"wav")
    assert stt.json() == {"text": "qué hora es"}
    turn = client.post("/api/v1/voice/turn", headers=AUTH, content=b"wav").json()
    assert turn["transcript"] == "qué hora es" and turn["reply"] == "Hola Gabriel"
    assert turn["audio_mime"] == "audio/wav" and turn["audio_base64"]


def test_task_endpoint_validates_and_queues(client, monkeypatch):
    from backend.core import remote_tasks

    async def no_run(task_id):  # el loop del TestClient se cierra tras cada request
        return {}

    monkeypatch.setattr(remote_tasks, "run_task", no_run)
    created = client.post("/api/v1/tasks", headers=AUTH, json={"prompt": "resume mi correo", "title": "Correo"})
    assert created.status_code == 202 and created.json()["task_id"].startswith("tsk_")
    bad = client.post("/api/v1/tasks", headers=AUTH, json={"prompt": "x", "schedule": {"interval_seconds": 5}})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"
    assert client.get("/api/v1/tasks/tsk_nada", headers=AUTH).status_code == 404


async def test_background_task_runs_and_stores_the_result(agent, monkeypatch):
    from backend.core import remote_tasks

    notified = []

    class _Gateway:
        async def notify(self, **kw):
            notified.append(kw)

    monkeypatch.setattr("backend.core.gateway_service.get_gateway", lambda: _Gateway())
    task = await remote_tasks.create_task(prompt="resume mi correo", title="Correo", notify=["telegram:123"])
    await asyncio.gather(*list(remote_tasks._running))
    stored = await remote_tasks.get_task(task["task_id"])
    assert stored["status"] == "done" and stored["result"] == "Hola Gabriel" and stored["runs"] == 1
    assert "resume mi correo" in agent.prompts[-1]
    assert notified[0]["target"] == "telegram:123" and notified[0]["body"] == "Hola Gabriel"
    assert await remote_tasks.cancel_task(task["task_id"])
    assert (await remote_tasks.get_task(task["task_id"]))["status"] == "cancelled"


async def test_tasks_left_pending_by_a_restart_are_recovered(agent, monkeypatch):
    import time

    from backend.core import remote_tasks

    monkeypatch.setattr("backend.core.gateway_service.get_gateway", lambda: None)
    await remote_tasks._ensure_db()
    rows = [
        ("tsk_cola", "queued", None),
        ("tsk_corriendo", "running", None),
        ("tsk_programada", "running", '{"interval_seconds": 3600}'),
    ]
    async with remote_tasks._db() as db:
        for task_id, status, schedule in rows:
            await db.execute(
                "INSERT INTO remote_tasks (task_id, title, prompt, status, schedule_json, notify_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, '[]', ?)", (task_id, task_id, "resume mi correo", status, schedule, time.time()),
            )
        await db.commit()

    counts = await remote_tasks.recover_after_restart()
    await asyncio.gather(*list(remote_tasks._running))

    assert counts == {"requeued": 1, "interrupted": 1, "rescheduled": 1}
    assert (await remote_tasks.get_task("tsk_cola"))["status"] == "done"
    interrupted = await remote_tasks.get_task("tsk_corriendo")
    assert interrupted["status"] == "failed" and "reinició" in interrupted["error"]
    assert (await remote_tasks.get_task("tsk_programada"))["status"] == "scheduled"


def test_websocket_chat_and_node_registration(client):
    with client.websocket_connect(f"/api/v1/ws?token={TOKEN}", headers=LOCAL_HOST) as ws:
        ws.send_text(json.dumps({"type": "hello", "client": "test"}))
        assert json.loads(ws.receive_text())["type"] == "ready"
        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text())["type"] == "pong"
        ws.send_text(json.dumps({"type": "node.register", "surfaces": ["display.face", "led.set"]}))
        assert json.loads(ws.receive_text())["type"] == "node.registered"
        ws.send_text(json.dumps({"type": "chat", "id": "c1", "text": "hola"}))
        frames = []
        while not frames or frames[-1]["type"] != "done":
            frames.append(json.loads(ws.receive_text()))
        assert frames[-1]["reply"] == "Hola Gabriel" and all(f.get("id") == "c1" for f in frames if f["type"] == "chunk")


def test_websocket_rejects_missing_token(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/ws", headers=LOCAL_HOST) as ws:
            ws.receive_text()


def test_reply_hides_action_markup_and_keeps_the_conclusion():
    from backend.api.v1 import ChatResult, translate_event

    result = ChatResult()
    chunks = []
    for piece in ["Voy a revisar.", "[ACT", "ION:browser_open(url=\"x\")]", " Listo"]:
        chunks += translate_event("agent:message", {"text": piece, "type": "text", "done": False}, result)
    chunks += translate_event("agent:message", {"text": "", "type": "text", "done": True}, result)
    for piece in ["Lima", "[ACTION:task_complete(summary=\"Lima\")]"]:
        chunks += translate_event("agent:message", {"text": piece, "type": "text", "done": False}, result)
    chunks += translate_event("agent:message", {"text": "", "type": "text", "done": True}, result)
    streamed = "".join(c["text"] for c in chunks)
    assert "ACTION" not in streamed and "[" not in streamed
    assert streamed == "Voy a revisar. ListoLima"
    assert result.reply == "Lima" and result.segments == ["Voy a revisar. Listo", "Lima"]
