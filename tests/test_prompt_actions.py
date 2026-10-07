"""Los prompts solo nombran acciones que el planner sabe ejecutar (o tools de un MCP)."""

from __future__ import annotations

import re

from backend.config import CODE_DIR

PROMPTS = ("data/prompts/system_prompt.md", "data/prompts/task_request_hint.md")
# Tools de servidores MCP que el prompt menciona para cuando están configurados.
MCP_TOOLS = {
    "click_mouse", "move_mouse", "drag_mouse", "scroll_mouse", "type_text", "press_key", "press_key_combination",
    "hold_key", "get_screenshot", "get_screen_size", "get_cursor_position", "get_active_window", "focus_window",
    "minimize_window", "restore_window", "resize_window", "reposition_window", "get_clipboard_content",
    "set_clipboard_content", "has_clipboard_text", "clear_clipboard", "click_at", "evaluate_script",
    "take_snapshot", "fill", "list_tools", "list_servers", "mcp_tool",
}
PARAMS = {
    "query_text", "element_type", "expected_text", "verify_text", "duration_ms", "interval_seconds", "server_id",
    "timeout_seconds", "account_id", "payment_account_id", "max_retries", "retry_backoff_seconds",
    "retry_backoff_multiplier", "skill_id", "app_label", "event_name", "webhook_path", "heartbeat_key",
}


def _planner_actions() -> set[str]:
    code = (CODE_DIR / "backend" / "core" / "planner.py").read_text(encoding="utf-8")
    return set(re.findall(r'case "([a-z0-9_]+)"', code)) | set(re.findall(r'\| "([a-z0-9_]+)"', code))


def test_prompts_only_name_real_actions():
    known = _planner_actions() | MCP_TOOLS | PARAMS
    missing = {}
    for rel in PROMPTS:
        text = (CODE_DIR / rel).read_text(encoding="utf-8")
        names = set(re.findall(r"`([a-z][a-z0-9_]{3,})\(", text))
        names |= {n for n in re.findall(r"`([a-z][a-z0-9_]{3,})`", text) if "_" in n}
        unknown = sorted(names - known)
        if unknown:
            missing[rel] = unknown
    assert not missing, f"Acciones nombradas en prompts que no existen: {missing}"
