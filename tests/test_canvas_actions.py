"""Canvas: el agente crea y actualiza tableros que la pestaña Canvas muestra (y se guardan)."""

from __future__ import annotations

import pytest

from backend.core import canvas as canvas_module
from backend.core.planner import Action, ActionPlanner


@pytest.fixture
def fresh_canvas(tmp_path, monkeypatch):
    # La config trae canvas.db_path: hay que redirigir la ruta resuelta, no solo el default.
    monkeypatch.setattr(canvas_module.CanvasService, "_resolve_db_path", staticmethod(lambda: tmp_path / "gateway.db"))
    monkeypatch.setattr(canvas_module, "_canvas_service", None)
    yield tmp_path / "gateway.db"
    canvas_module._canvas_service = None


async def run(kind, **params):
    planner = ActionPlanner.__new__(ActionPlanner)
    return await planner._execute_single(Action(type=kind, params=params))


async def test_create_update_and_list(fresh_canvas):
    created = await run("canvas_create", title="Ventas de hoy", type="dashboard",
                        data={"cards": [{"label": "Pedidos", "value": "12", "change": "+3"}]})
    assert created["success"], created["message"]
    canvas_id = created["data"]["canvas_id"]

    updated = await run("canvas_update", canvas_id=canvas_id,
                        data={"cards": [{"label": "Pedidos", "value": "15", "change": "+6"}]})
    assert updated["success"] and updated["data"]["version"] == 2

    svc = await canvas_module.ensure_canvas_service()
    html = (await svc.get_canvas(canvas_id)).content
    assert "15" in html and "Pedidos" in html

    listed = await run("canvas_list")
    assert [c["title"] for c in listed["data"]["canvases"]] == ["Ventas de hoy"]


async def test_values_are_escaped_and_bad_input_is_explained(fresh_canvas):
    created = await run("canvas_create", title="Lista", type="list",
                        data={"items": [{"text": "<img src=x onerror=alert(1)>", "meta": "web"}]})
    svc = await canvas_module.ensure_canvas_service()
    html = (await svc.get_canvas(created["data"]["canvas_id"])).content
    assert "<img" not in html and "&lt;img" in html

    assert "inválido" in (await run("canvas_create", title="x", type="pizarra"))["message"]
    assert "objeto JSON" in (await run("canvas_create", title="x", type="status", data="texto"))["message"]
    assert "No existe" in (await run("canvas_update", canvas_id="nada", data={}))["message"]


async def test_canvases_survive_a_restart(fresh_canvas):
    created = await run("canvas_create", title="Monitor", type="monitor",
                        data={"metrics": [{"name": "CPU", "percent": 40, "value": "40%"}]})
    canvas_module._canvas_service = None  # como si el núcleo se reiniciara
    listed = await run("canvas_list")
    assert listed["data"]["canvases"][0]["canvas_id"] == created["data"]["canvas_id"]


async def test_socket_subscription_gets_snapshot_and_live_updates(fresh_canvas, monkeypatch):
    from backend.api import websocket_handler

    sent = []

    async def emit(event, data=None, to=None, **kwargs):
        sent.append((event, to))

    monkeypatch.setattr(websocket_handler.sio, "emit", emit)
    created = await run("canvas_create", title="Estado", type="status", data={"status": "empezando"})
    canvas_module._canvas_service = None  # la app abre la pestaña tras reiniciar el núcleo
    await websocket_handler.canvas_subscribe("sid-ui", {"canvas_id": created["data"]["canvas_id"]})
    assert ("canvas:snapshot", "sid-ui") in sent
    await run("canvas_update", canvas_id=created["data"]["canvas_id"], data={"status": "listo"})
    assert ("canvas:updated", "sid-ui") in sent
