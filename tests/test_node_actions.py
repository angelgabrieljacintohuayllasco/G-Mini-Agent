"""Acciones de nodo del planner (node_list/node_invoke/node_events) y helpers del NodeManager."""

from __future__ import annotations

import pytest

from backend.core import node_manager as node_manager_module
from backend.core.node_manager import NodeInfo, NodeManager, NodeStatus
from backend.core.planner import Action, ActionPlanner


def _node(node_id: str, name: str, surfaces: list[str], sid: str | None = "sid-1") -> NodeInfo:
    return NodeInfo(
        node_id=node_id,
        name=name,
        node_type="android",
        status=NodeStatus.CONNECTED.value,
        surfaces=surfaces,
        permissions={s: True for s in surfaces},
        ws_sid=sid,
    )


def test_find_node_by_id_and_name():
    nm = NodeManager()
    node = _node("dev_1", "Pixel de Gabriel", ["sms.send"])
    nm._nodes[node.node_id] = node
    assert nm.find_node("dev_1") is node
    assert nm.find_node("pixel de gabriel") is node
    assert nm.find_node("no existe") is None


def test_pick_node_for_surface_requires_exactly_one():
    nm = NodeManager()
    a = _node("a", "A", ["sms.send"], sid="sa")
    b = _node("b", "B", ["device.tap"], sid="sb")
    nm._nodes["a"] = a
    nm._nodes["b"] = b
    assert nm.pick_node_for_surface("sms.send") is a
    assert nm.pick_node_for_surface("device.tap") is b
    # Dos nodos con la misma superficie: ambiguo.
    b.surfaces.append("sms.send")
    b.permissions["sms.send"] = True
    assert nm.pick_node_for_surface("sms.send") is None


def test_record_and_read_events():
    nm = NodeManager()
    node = _node("dev_1", "Pixel", ["sms.send"], sid="sid-1")
    nm._nodes["dev_1"] = node
    nm._sid_to_node["sid-1"] = "dev_1"
    nm.record_event("sid-1", "sms.received", {"from": "+51999", "text": "codigo 123"})
    events = nm.get_recent_events("dev_1")
    assert len(events) == 1 and events[0]["event"] == "sms.received"
    assert events[0]["data"]["text"] == "codigo 123"
    # Un sid desconocido no guarda nada.
    assert nm.record_event("otro", "x", {}) is None


class _FakeManager:
    def __init__(self, node: NodeInfo | None, invoke_result: dict):
        self._node = node
        self._invoke_result = invoke_result
        self.calls: list[tuple] = []

    async def list_nodes(self, include_disconnected: bool = True):
        return [self._node.to_dict()] if self._node else []

    def find_node(self, ref: str):
        return self._node if self._node and ref in (self._node.node_id, self._node.name) else None

    def pick_node_for_surface(self, surface: str):
        return self._node if self._node and self._node.is_surface_allowed(surface) else None

    async def get_node(self, node_id: str):
        return self._node if self._node and self._node.node_id == node_id else None

    def get_recent_events(self, node_id: str, limit: int = 20):
        return [{"event": "sms.received", "data": {"text": "hola"}}]

    async def invoke_surface(self, node_id, surface, params=None, timeout=30.0):
        self.calls.append((node_id, surface, params, timeout))
        return self._invoke_result


async def test_node_invoke_sends_to_the_phone(monkeypatch):
    node = _node("dev_1", "Pixel", ["sms.send"])
    fake = _FakeManager(node, {"ok": True, "data": {"ok": True}})
    monkeypatch.setattr(node_manager_module, "get_node_manager", lambda: fake)
    planner = ActionPlanner.__new__(ActionPlanner)

    result = await planner._execute_single(
        Action(type="node_invoke", params={"surface": "sms.send", "params": {"to": "+51999", "text": "hola"}}),
    )
    assert result["success"]
    # sms.send usa un timeout largo porque el telefono pide confirmacion.
    assert fake.calls[0][1] == "sms.send" and fake.calls[0][3] == 180.0


async def test_node_invoke_fails_without_a_matching_node(monkeypatch):
    fake = _FakeManager(None, {"ok": False})
    monkeypatch.setattr(node_manager_module, "get_node_manager", lambda: fake)
    planner = ActionPlanner.__new__(ActionPlanner)
    result = await planner._execute_single(
        Action(type="node_invoke", params={"surface": "sms.send", "params": {"to": "x", "text": "y"}}),
    )
    assert not result["success"]


async def test_node_list_reports_surfaces(monkeypatch):
    node = _node("dev_1", "Pixel", ["sms.send", "device.tap"])
    fake = _FakeManager(node, {"ok": True})
    monkeypatch.setattr(node_manager_module, "get_node_manager", lambda: fake)
    planner = ActionPlanner.__new__(ActionPlanner)
    result = await planner._execute_single(Action(type="node_list", params={}))
    assert result["success"]
    assert result["data"]["nodes"][0]["surfaces"] == ["sms.send", "device.tap"]
