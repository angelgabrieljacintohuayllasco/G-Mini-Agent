"""Runtime MCP: respuestas por id, peticiones del servidor, EOF y caché negativa."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from backend.core import mcp_runtime as mcp_module
from backend.core.mcp_runtime import MCPRuntime

FAKE_SERVER = r'''
import json, os, sys, threading, time

lock = threading.Lock()
replies = {}
got_reply = threading.Event()


def send(obj):
    with lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


TOOLS = [
    {"name": "echo", "description": "eco", "inputSchema": {"type": "object",
        "properties": {"text": {"type": "string"}, "delay": {"type": "number"}}, "required": ["text"]}},
    {"name": "ping_client", "description": "ping", "inputSchema": {"type": "object"}},
    {"name": "die", "description": "muere", "inputSchema": {"type": "object"}},
]


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2024-11-05", "capabilities": {}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments") or {}
        if name == "die":
            os._exit(0)
        if name == "ping_client":
            send({"jsonrpc": "2.0", "id": "srv-ping", "method": "ping"})
            text = "pong-ok" if got_reply.wait(5) and "result" in replies.get("srv-ping", {}) else "pong-missing"
        else:
            time.sleep(float(args.get("delay", 0)))
            text = str(args.get("text"))
        send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}]}})


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    if "method" not in msg:
        replies[msg.get("id")] = msg
        got_reply.set()
        continue
    if "id" not in msg:
        continue
    threading.Thread(target=handle, args=(msg,), daemon=True).start()
'''


class _FakeRegistry:
    def __init__(self, script: Path):
        self.script = script
        self.spawned: list[str] = []

    def _server(self, server_id: str) -> dict:
        args = [str(self.script)] if server_id == "fake" else ["-c", "import sys; sys.exit(3)"]
        return {
            "id": server_id, "name": server_id, "transport": "stdio", "ready": True,
            "command": sys.executable, "resolved_command": sys.executable,
            "args": args, "env": {}, "cwd": None,
        }

    def get_runtime_server(self, server_id: str) -> dict:
        self.spawned.append(server_id)
        return self._server(server_id)

    def list_servers(self) -> dict:
        return {"enabled": True, "servers": [self._server("fake"), self._server("broken")]}


@pytest.fixture()
def runtime(tmp_path):
    script = tmp_path / "fake_mcp.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    rt = MCPRuntime(_FakeRegistry(script))
    yield rt
    rt.shutdown()


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def test_concurrent_calls_get_their_own_response(runtime):
    runtime.list_tools("fake", timeout_seconds=15)
    results: dict[str, object] = {}

    def call(label: str, delay: float) -> None:
        try:
            results[label] = runtime.call_tool("fake", "echo", {"text": label, "delay": delay}, timeout_seconds=15)
        except Exception as exc:  # pragma: no cover - se reporta en el assert
            results[label] = exc

    slow = threading.Thread(target=call, args=("lento", 1.0))
    fast = threading.Thread(target=call, args=("rapido", 0.0))
    slow.start()
    time.sleep(0.2)
    fast.start()
    slow.join(20)
    fast.join(20)
    assert _text(results["lento"]) == "lento"
    assert _text(results["rapido"]) == "rapido"


def test_server_ping_is_answered(runtime):
    runtime.list_tools("fake", timeout_seconds=15)
    result = runtime.call_tool("fake", "ping_client", {}, timeout_seconds=15)
    assert _text(result) == "pong-ok"


def test_server_exit_fails_fast(runtime):
    runtime.list_tools("fake", timeout_seconds=30)
    started = time.monotonic()
    with pytest.raises(RuntimeError):
        runtime.call_tool("fake", "die", {}, timeout_seconds=30)
    assert time.monotonic() - started < 8


def test_failed_discovery_is_not_retried_every_prompt(runtime):
    first = runtime.discover_all_tools(timeout_seconds=5)
    assert [t["name"] for t in first["fake"]][0] == "echo"
    assert first["broken"] == []
    spawned = list(runtime._registry.spawned)
    second = runtime.discover_all_tools(timeout_seconds=5)
    assert second["broken"] == []
    assert runtime._registry.spawned == spawned  # ni fake (caché) ni broken (backoff) se relanzan


def test_cached_summary_never_spawns(runtime):
    assert runtime.get_cached_tools_summary() == ""
    assert runtime._registry.spawned == []
    runtime.list_tools("fake", timeout_seconds=15)
    summary = runtime.get_cached_tools_summary()
    assert "Servidor MCP: `fake`" in summary and "**echo**" in summary


def test_shared_runtime_is_single_instance(monkeypatch):
    monkeypatch.setattr(mcp_module, "_shared_runtime", None)
    first = mcp_module.get_mcp_runtime()
    assert mcp_module.get_mcp_runtime() is first
    mcp_module.shutdown_mcp_runtime()
    assert mcp_module._shared_runtime is None
