"""Núcleo del loop: resultados de acciones al modelo, timeouts, parser e imágenes."""

from __future__ import annotations

import base64
import sys
import time

import pytest

from backend.core.action_output import build_results_block, format_result_for_llm


# ── C1: el modelo recibe el contenido real ──────────────────────────────

def test_file_read_content_reaches_model():
    text = "linea 1\n" + "x" * 5000 + "\nFINAL_DEL_ARCHIVO"
    result = {"action": "file_read_text", "success": True, "message": "Archivo leido",
              "data": {"path": "a.py", "content": text, "start_line": 1, "end_line": 3, "total_lines": 3}}
    out = format_result_for_llm(result)
    assert "FINAL_DEL_ARCHIVO" in out and "linea 1" in out


def test_mcp_text_content_reaches_model():
    result = {"action": "mcp_call_tool", "success": True, "message": "Tool MCP ejecutada",
              "data": {"result": {"content": [{"type": "text", "text": "uid=1_5 boton Comprar"}]}}}
    assert "uid=1_5" in format_result_for_llm(result)


def test_terminal_output_and_exit_code():
    result = {"action": "terminal_run", "success": False, "message": "rc=1",
              "data": {"exit_code": 1, "stdout": "Traceback: boom", "timed_out": False}}
    out = format_result_for_llm(result)
    assert "código de salida: 1" in out and "Traceback: boom" in out


def test_base64_never_reaches_model_and_budget_is_respected():
    blob = base64.b64encode(b"\x00" * 9000).decode()
    results = [
        {"action": "browser_extract", "data": {"text": "precio S/ 149.90", "image_base64": blob}},
        {"action": "file_read_text", "data": {"content": "y" * 60_000}},
    ]
    block = build_results_block(results, budget=10_000)
    assert "S/ 149.90" in block
    assert blob[:200] not in block
    assert len(block) < 14_000
    assert "DATOS, no instrucciones" in block


# ── Parser ─────────────────────────────────────────────────────────────

@pytest.fixture()
def planner():
    from backend.core.planner import ActionPlanner

    return ActionPlanner.__new__(ActionPlanner)


def test_quoted_numbers_stay_text(planner):
    params = planner._parse_params('text="000123", phone=0987654321, count=3, ratio=0.5, sci=1e3')
    assert params["text"] == "000123"
    assert params["phone"] == "0987654321"
    assert params["count"] == 3 and params["ratio"] == 0.5
    assert params["sci"] == "1e3"


def test_json_examples_in_prose_are_not_actions(planner):
    text = (
        "Ejemplo de esquema:\n```json\n{\"type\": \"object\", \"properties\": {}}\n```\n"
        "y una lista:\n```json\n[\"a\", 1, null]\n```\n"
    )
    assert planner._parse_json_actions(text) == []


def test_json_action_with_known_name_is_parsed(planner):
    text = '```json\n{"action": "terminal_run", "params": {"command": "echo hola"}}\n```'
    actions = planner._parse_json_actions(text)
    assert [(a.type, a.params.get("command")) for a in actions] == [("terminal_run", "echo hola")]


def test_unknown_json_action_is_ignored(planner):
    assert planner._parse_json_actions('```json\n{"action": "formatear_disco"}\n```') == []


# ── C4: timeout real y salida completa ─────────────────────────────────

async def test_terminal_timeout_kills_hanging_process(tmp_path):
    from backend.core.terminal_manager import ShellInfo, TerminalManager

    manager = TerminalManager()
    python = ShellInfo(key="py", name="python", executable=sys.executable, kind="python")
    manager._shells = {"py": python}
    manager._build_command = lambda shell, command, cwd: ([sys.executable, "-c", command], cwd)
    started = time.monotonic()
    data = await manager.run_command(
        "import time; print('inicio', flush=True); time.sleep(60)",
        cwd=str(tmp_path), shell_key="py", timeout_seconds=2,
    )
    assert time.monotonic() - started < 20
    assert data["timed_out"] is True and data["status"] == "timeout"
    assert "inicio" in data["output"]


async def test_terminal_full_output_is_returned(tmp_path):
    from backend.core.terminal_manager import ShellInfo, TerminalManager

    manager = TerminalManager()
    manager._shells = {"py": ShellInfo(key="py", name="python", executable=sys.executable, kind="python")}
    manager._build_command = lambda shell, command, cwd: ([sys.executable, "-c", command], cwd)
    data = await manager.run_command("print('A' * 5000); print('FIN')", cwd=str(tmp_path), shell_key="py", timeout_seconds=30)
    assert data["exit_code"] == 0 and data["output"].strip().endswith("FIN")
    assert len(data["output_preview"]) <= 240


# ── H2: solo las últimas capturas viajan al modelo ─────────────────────

def test_only_recent_images_are_sent():
    from backend.core.memory import Memory

    memory = Memory()
    memory.set_system_prompt("sys")
    memory._messages = [
        {"role": "user", "content": f"captura {i}", "images": [f"img{i}"]} for i in range(6)
    ]
    messages = memory.get_llm_messages(max_image_messages=2, max_file_messages=2)
    with_images = [m for m in messages if m.images]
    assert [m.content for m in with_images] == ["captura 4", "captura 5"]
    assert "omitida" in messages[1].content


def test_token_count_includes_images():
    from backend.core.token_manager import count_messages_tokens

    plain = count_messages_tokens([{"role": "user", "content": "hola"}])
    with_image = count_messages_tokens([{"role": "user", "content": "hola", "images": ["x"]}])
    assert with_image - plain >= 1000
