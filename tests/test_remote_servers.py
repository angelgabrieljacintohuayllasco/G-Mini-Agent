"""Otros G-Mini emparejados: emparejar con código, delegar tareas y esperar el resultado."""

from __future__ import annotations

import json

import pytest
from aiohttp import web

from backend.core import remote_servers
from backend.core.remote_servers import RemoteServerError

TOKEN = "gm_dev_prueba"


@pytest.fixture
async def remote(tmp_path, monkeypatch):
    keys: dict[str, str] = {}
    monkeypatch.setattr(remote_servers.config, "set_api_key", lambda name, value: keys.__setitem__(name, value))
    monkeypatch.setattr(remote_servers.config, "get_api_key", lambda name: keys.get(name))
    monkeypatch.setattr(remote_servers.config, "delete_api_key", lambda name: keys.pop(name, None))
    monkeypatch.setattr(remote_servers, "STORE", tmp_path / "remote_servers.json")
    state: dict = {"tasks": {}, "claims": []}

    def authorized(request):
        return request.headers.get("Authorization") == f"Bearer {TOKEN}"

    async def claim(request):
        body = await request.json()
        state["claims"].append(body)
        if body["code"] != "123456":
            return web.json_response({"error": {"code": "invalid_code", "message": "Código inválido"}}, status=403)
        return web.json_response({"token": TOKEN, "device_id": "dev_1", "server_name": "tv-server", "agent_name": "G-Mini"})

    async def create_task(request):
        if not authorized(request):
            return web.json_response({"error": {"message": "Token inválido"}}, status=401)
        body = await request.json()
        task_id = f"tsk_{len(state['tasks']) + 1}"
        state["tasks"][task_id] = {"task_id": task_id, "status": "queued", "prompt": body["prompt"], "polls": 0}
        return web.json_response({"task_id": task_id, "status": "queued"}, status=202)

    async def get_task(request):
        if not authorized(request):
            return web.json_response({"error": {"message": "Token inválido"}}, status=401)
        task = state["tasks"][request.match_info["task_id"]]
        task["polls"] += 1
        if task["polls"] >= 2:
            task.update(status="done", result="Listo: " + task["prompt"])
        return web.json_response({k: v for k, v in task.items() if k != "polls"})

    async def health(request):
        return web.json_response({"ok": True, "mode": "server"})

    async def me(request):
        return web.json_response({"kind": "device"}) if authorized(request) else web.json_response({}, status=401)

    app = web.Application()
    app.add_routes([
        web.post("/api/v1/pairing/claim", claim),
        web.post("/api/v1/tasks", create_task),
        web.get("/api/v1/tasks/{task_id}", get_task),
        web.get("/api/v1/health", health),
        web.get("/api/v1/me", me),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield {"address": f"127.0.0.1:{port}", "state": state, "keys": keys, "store": tmp_path / "remote_servers.json"}
    await runner.cleanup()


def test_normalize_url():
    assert remote_servers.normalize_url("100.71.131.70") == "http://100.71.131.70:8765"
    assert remote_servers.normalize_url("tv-server:9000/") == "http://tv-server:9000"
    assert remote_servers.normalize_url("https://gmini.example.com") == "https://gmini.example.com"
    with pytest.raises(RemoteServerError):
        remote_servers.normalize_url("  ")


async def test_pairing_keeps_the_token_out_of_the_list(remote):
    with pytest.raises(RemoteServerError, match="Código inválido"):
        await remote_servers.pair(remote["address"], "000000")
    with pytest.raises(RemoteServerError, match="6 dígitos"):
        await remote_servers.pair(remote["address"], "12")

    server = await remote_servers.pair(remote["address"], "123 456")
    assert server["name"] == "tv-server" and server["url"].startswith("http://127.0.0.1:")
    assert remote["keys"] == {f"remote_server_{server['id']}": TOKEN}
    assert TOKEN not in remote["store"].read_text(encoding="utf-8")
    assert remote["state"]["claims"][-1]["device_type"] == "desktop"

    again = await remote_servers.pair(remote["address"], "123456", name="Servidor TV")
    assert [s["name"] for s in remote_servers.list_servers()] == ["Servidor TV"]
    assert list(remote["keys"]) == [f"remote_server_{again['id']}"]  # el token viejo se borra


async def test_delegate_and_wait_for_the_result(remote):
    await remote_servers.pair(remote["address"], "123456")
    queued = await remote_servers.delegate("tv-server", "resume las noticias")
    assert queued["status"] == "queued" and queued["server"] == "tv-server"

    done = await remote_servers.delegate("", "ordena las descargas", wait=True, poll_s=0.01)
    assert done["status"] == "done" and done["result"] == "Listo: ordena las descargas"
    status = await remote_servers.task_status("tv-server", queued["task_id"])
    assert status["task_id"] == queued["task_id"]
    health = await remote_servers.status("tv-server")
    assert health["health"]["mode"] == "server" and health["me"]["kind"] == "device"


async def test_errors_are_readable(remote):
    with pytest.raises(RemoteServerError, match="No hay otros G-Mini"):
        await remote_servers.delegate("", "algo")
    server = await remote_servers.pair(remote["address"], "123456")
    remote["keys"].clear()
    with pytest.raises(RemoteServerError, match="vuelve a emparejarlo"):
        await remote_servers.delegate("tv-server", "algo")
    remote_servers.remove(server["id"])
    assert remote_servers.list_servers() == []
    with pytest.raises(RemoteServerError, match="No pude conectar"):
        await remote_servers.pair("127.0.0.1:1", "123456")


async def test_prompt_index_and_policy(remote):
    assert remote_servers.build_prompt_index() == ""
    await remote_servers.pair(remote["address"], "123456")
    index = remote_servers.build_prompt_index()
    assert "tv-server" in index and "remote_delegate" in index

    from backend.core.planner import Action
    from backend.core.policy import PolicyEngine

    policy = PolicyEngine.__new__(PolicyEngine)
    assert policy._classify(Action(type="remote_delegate", params={"task": "x"}))["severity"] == "medium"
    assert policy._classify(Action(type="remote_list", params={}))["severity"] == "low"
    assert json.loads(remote["store"].read_text(encoding="utf-8"))[0]["name"] == "tv-server"
