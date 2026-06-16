"""
G-Mini Agent — Action Planner.
Interpreta instrucciones del LLM y las traduce en acciones ejecutables.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from backend.automation.pc_controller import AutomationEngine
from backend.automation.adb_controller import ADBController
from backend.automation.editor_bridge import EditorBridge
from backend.vision.engine import VisionEngine
from backend.automation.browser_controller import BrowserController
from backend.config import config
from backend.core.resilience import ActionAttemptRecord, ActionExecutionResultModel, RetryPolicy
from backend.core.ide_manager import IDEManager
from backend.core.cost_tracker import get_cost_tracker
from backend.core.gateway_service import get_gateway
from backend.core.mcp_registry import MCPRegistry, get_mcp_registry
from backend.core.mcp_runtime import MCPRuntime
from backend.core.payment_registry import PaymentRegistry
from backend.core.scheduler import get_scheduler
from backend.core.skill_registry import SkillRegistry
from backend.core.skill_runtime import SkillRuntime
from backend.core.terminal_manager import TerminalManager
from backend.core.workspace_manager import WorkspaceManager

# Import for emitting action events
_sio = None
_current_sid = None

def set_planner_socket(sio, sid):
    """Configura el socket y sid para emitir eventos de acción."""
    global _sio, _current_sid
    _sio = sio
    _current_sid = sid


async def _emit_action_start(action_id: str, action_type: str, params: dict) -> None:
    """Emite el INICIO de una accion: crea la tarjeta (con SVG) en el frontend.

    Lleva un `actionId` para que el frontend pueda emparejar el resultado
    (`agent:action_result`) y DETENER el contador de tiempo de la tarjeta.
    Tambien dispara los efectos visuales del overlay (segun el tipo).
    """
    global _sio, _current_sid
    if _sio and _current_sid:
        try:
            await _sio.emit("agent:action", {
                "actionId": action_id,
                "type": action_type,
                "params": params,
            }, to=_current_sid)
        except Exception as exc:
            logger.debug(f"No se pudo emitir agent:action ({action_type}): {exc}")


async def _emit_action_result(
    action_id: str,
    action_type: str,
    success: bool,
    message: str,
    duration_ms: float | None = None,
) -> None:
    """Emite el RESULTADO de una accion: actualiza la tarjeta (OK/ERROR),
    detiene el contador y guarda la duracion exacta. Sin esto el contador
    'Esperando Ns' corria infinitamente."""
    global _sio, _current_sid
    if _sio and _current_sid:
        try:
            await _sio.emit("agent:action_result", {
                "actionId": action_id,
                "type": action_type,
                "success": bool(success),
                "result": str(message or ""),
                "durationMs": round(float(duration_ms), 1) if duration_ms is not None else None,
            }, to=_current_sid)
        except Exception as exc:
            logger.debug(f"No se pudo emitir agent:action_result ({action_type}): {exc}")


async def _emit_action_event(action_type: str, params: dict):
    """DEPRECADO. La creacion de tarjetas ahora la centraliza `execute_actions`
    via `_emit_action_start` (con actionId) + `_emit_action_result`. Se mantiene
    como no-op para no romper las llamadas por-caso existentes en `_execute_single`
    y evitar tarjetas duplicadas sin actionId (que dejaban el contador colgado)."""
    return None


@dataclass
class Action:
    """Una acción que el agente puede ejecutar."""
    type: str  # click, type, scroll, hotkey, screenshot, wait, etc.
    params: dict[str, Any]
    description: str = ""


ACTION_TOKEN = "[ACTION:"

ACTION_TYPE_ALIASES = {
    "workspace_list_files": "file_list",
    "workspace_read_file": "file_read_text",
    "workspace_read_text": "file_read_text",
    "workspace_read_batch": "file_read_batch",
    "workspace_search_text": "file_search_text",
    "workspace_replace_text": "file_replace_text",
    "workspace_write_file": "file_write_text",
    "workspace_file_exists": "file_exists",
    "repo_snapshot": "workspace_snapshot",
    "project_snapshot": "workspace_snapshot",
    "workspace_git_status": "git_status",
    "repo_git_status": "git_status",
    "repo_changed_files": "git_changed_files",
    "workspace_git_changed_files": "git_changed_files",
    "repo_diff": "git_diff",
    "workspace_git_diff": "git_diff",
    "repo_log": "git_log",
    "workspace_git_log": "git_log",
    "repo_outline": "code_outline",
    "file_outline": "code_outline",
    "code_symbols": "code_outline",
    "related_files": "code_related_files",
    "repo_related_files": "code_related_files",
    "code_related": "code_related_files",
    "skills_list": "skills_catalog",
    "list_skills": "skills_catalog",
    "skill_registry": "skills_catalog",
    "install_skill_local": "skill_install_local",
    "install_skill_git": "skill_install_git",
    "enable_skill": "skill_enable",
    "disable_skill": "skill_disable",
    "remove_skill": "skill_uninstall",
    "uninstall_skill": "skill_uninstall",
    "run_skill": "skill_run",
    "skill_execute": "skill_run",
    "mcp_servers": "mcp_list_servers",
    "mcp_status": "mcp_list_servers",
    "list_mcps": "mcp_list_servers",
    "mcp_tools": "mcp_list_tools",
    "list_mcp_tools": "mcp_list_tools",
    "mcp_run_tool": "mcp_call_tool",
    "call_mcp_tool": "mcp_call_tool",
    "scheduler_jobs": "schedule_list_jobs",
    "list_scheduler_jobs": "schedule_list_jobs",
    "schedule_jobs": "schedule_list_jobs",
    "create_scheduler_job": "schedule_create_job",
    "update_scheduler_job": "schedule_update_job",
    "delete_scheduler_job": "schedule_delete_job",
    "run_scheduler_job": "schedule_run_job",
    "scheduler_runs": "schedule_list_runs",
    "scheduler_checkpoints": "schedule_list_checkpoints",
    "list_scheduler_checkpoints": "schedule_list_checkpoints",
    "schedule_checkpoints": "schedule_list_checkpoints",
    "scheduler_recovery": "schedule_recovery_status",
    "scheduler_recovery_status": "schedule_recovery_status",
    "costs_summary": "budget_summary",
    "budget_status": "budget_summary",
    "cost_summary": "budget_summary",
    "budget_weekly_report": "budget_weekly_report",
    "weekly_budget_report": "budget_weekly_report",
    "weekly_cost_report": "budget_weekly_report",
    "cost_weekly_report": "budget_weekly_report",
    "cost_events": "budget_list_events",
    "list_cost_events": "budget_list_events",
    "budget_events": "budget_list_events",
    "payments_accounts": "payments_list_accounts",
    "list_payment_accounts": "payments_list_accounts",
    "payment_accounts": "payments_list_accounts",
    "payment_account": "payments_get_account",
    "get_payment_account": "payments_get_account",
    "gateway_status": "gateway_status",
    "gateway_sessions": "gateway_list_sessions",
    "list_gateway_sessions": "gateway_list_sessions",
    "gateway_outbox": "gateway_list_outbox",
    "list_gateway_outbox": "gateway_list_outbox",
    "send_notification": "gateway_notify",
    "notify_user": "gateway_notify",
    "gateway_send": "gateway_notify",
    "emit_event": "schedule_emit_event",
    "emit_heartbeat": "schedule_emit_heartbeat",
    "trigger_webhook": "schedule_trigger_webhook",
    "fire_webhook": "schedule_trigger_webhook",
    "computer_use": "delegate_computer_use",
    "ui_task": "delegate_computer_use",
    "desktop_task": "delegate_computer_use",
    "editor_detect": "ide_detect",
    "vscode_detect": "ide_detect",
    "editor_open_workspace": "ide_open_workspace",
    "vscode_open_workspace": "ide_open_workspace",
    "editor_open_file": "ide_open_file",
    "vscode_open_file": "ide_open_file",
    "editor_open_diff": "ide_open_diff",
    "vscode_open_diff": "ide_open_diff",
    "editor_state": "ide_state",
    "vscode_state": "ide_state",
    "editor_active_file": "ide_active_file",
    "vscode_active_file": "ide_active_file",
    "current_file": "ide_active_file",
    "editor_selection": "ide_selection",
    "vscode_selection": "ide_selection",
    "selected_text": "ide_selection",
    "editor_workspace_folders": "ide_workspace_folders",
    "vscode_workspace_folders": "ide_workspace_folders",
    "workspace_folders": "ide_workspace_folders",
    "editor_diagnostics": "ide_diagnostics",
    "vscode_diagnostics": "ide_diagnostics",
    "editor_symbols": "ide_symbols",
    "vscode_symbols": "ide_symbols",
    "document_symbols": "ide_symbols",
    "active_symbols": "ide_symbols",
    "editor_find_symbol": "ide_find_symbol",
    "vscode_find_symbol": "ide_find_symbol",
    "find_symbol": "ide_find_symbol",
    "goto_symbol": "ide_find_symbol",
    "symbol_search": "ide_find_symbol",
    "editor_reveal_symbol": "ide_reveal_symbol",
    "vscode_reveal_symbol": "ide_reveal_symbol",
    "open_symbol": "ide_reveal_symbol",
    "editor_reveal_range": "ide_reveal_range",
    "vscode_reveal_range": "ide_reveal_range",
    "goto_range": "ide_reveal_range",
    "reveal_range": "ide_reveal_range",
    "editor_open_diagnostic": "ide_open_diagnostic",
    "vscode_open_diagnostic": "ide_open_diagnostic",
    "goto_diagnostic": "ide_open_diagnostic",
    "editor_next_diagnostic": "ide_next_diagnostic",
    "vscode_next_diagnostic": "ide_next_diagnostic",
    "next_problem": "ide_next_diagnostic",
    "editor_prev_diagnostic": "ide_prev_diagnostic",
    "vscode_prev_diagnostic": "ide_prev_diagnostic",
    "prev_problem": "ide_prev_diagnostic",
    "previous_problem": "ide_prev_diagnostic",
    "editor_apply_edit": "ide_apply_edit",
    "vscode_apply_edit": "ide_apply_edit",
    "editor_replace_range": "ide_apply_edit",
    "editor_apply_workspace_edits": "ide_apply_workspace_edits",
    "vscode_apply_workspace_edits": "ide_apply_workspace_edits",
    "editor_batch_edit": "ide_apply_workspace_edits",
    "vscode_batch_edit": "ide_apply_workspace_edits",
}

# Acciones de escritorio nativas (mouse/teclado directo) deshabilitadas para el
# agente principal (coordinador). El sub-agente de computer use usa pc_controller
# directamente y NO pasa por _execute_single, así que conserva control total.
# El coordinador debe usar MCPControl (mcp_call_tool) o delegate_computer_use.
_BLOCKED_MAIN_DESKTOP_ACTIONS = frozenset({
    "click", "double_click", "right_click", "type", "focus_type",
    "press", "key", "hotkey", "scroll", "move", "drag",
    "browser_desktop_fallback_click",
})


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y", "si", "sí", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off", ""}:
        return False
    return default


def _split_top_level_segments(raw: str, delimiter: str = ",") -> list[str]:
    segments: list[str] = []
    current: list[str] = []
    quote_char: str | None = None
    escape = False
    square_depth = 0
    paren_depth = 0
    brace_depth = 0

    for char in raw:
        if escape:
            current.append(char)
            escape = False
            continue

        if char == "\\":
            current.append(char)
            escape = True
            continue

        if quote_char:
            current.append(char)
            if char == quote_char:
                quote_char = None
            continue

        if char in {"'", '"'}:
            quote_char = char
            current.append(char)
            continue

        if char == "[":
            square_depth += 1
        elif char == "]":
            square_depth = max(0, square_depth - 1)
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(0, paren_depth - 1)
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)

        if (
            char == delimiter
            and quote_char is None
            and square_depth == 0
            and paren_depth == 0
            and brace_depth == 0
        ):
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            continue

        current.append(char)

    tail = "".join(current).strip()
    if tail:
        segments.append(tail)
    return segments


def _find_top_level_equals(raw: str) -> int:
    quote_char: str | None = None
    escape = False
    square_depth = 0
    paren_depth = 0
    brace_depth = 0

    for index, char in enumerate(raw):
        if escape:
            escape = False
            continue

        if char == "\\":
            escape = True
            continue

        if quote_char:
            if char == quote_char:
                quote_char = None
            continue

        if char in {"'", '"'}:
            quote_char = char
            continue

        if char == "[":
            square_depth += 1
            continue
        if char == "]":
            square_depth = max(0, square_depth - 1)
            continue
        if char == "(":
            paren_depth += 1
            continue
        if char == ")":
            paren_depth = max(0, paren_depth - 1)
            continue
        if char == "{":
            brace_depth += 1
            continue
        if char == "}":
            brace_depth = max(0, brace_depth - 1)
            continue

        if char == "=" and square_depth == 0 and paren_depth == 0 and brace_depth == 0:
            return index

    return -1


def _get_blocked_sites_config() -> tuple[bool, list[str]]:
    from backend.config import config as _cfg

    enabled = _coerce_bool(_cfg.get("agent", "blocked_sites_enabled", default=False), default=False)
    configured = _cfg.get("agent", "blocked_sites", default=None)
    if isinstance(configured, list):
        sites = configured
    else:
        sites = _cfg.get("agent", "banned_download_sites", default=[]) or []

    normalized = [
        str(site).strip().lower()
        for site in sites
        if str(site).strip()
    ]
    return enabled, normalized


def _normalize_match_text(value: Any) -> str:
    raw = str(value or "")
    normalized = unicodedata.normalize("NFKD", raw)
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _read_balanced_segment(text: str, start_index: int, open_char: str, close_char: str) -> tuple[str | None, int]:
    segment_chars: list[str] = []
    quote_char: str | None = None
    escape = False
    depth = 0

    for index in range(start_index, len(text)):
        char = text[index]
        segment_chars.append(char)

        if escape:
            escape = False
            continue

        if char == "\\":
            escape = True
            continue

        if quote_char:
            if char == quote_char:
                quote_char = None
            continue

        if char in {"'", '"'}:
            quote_char = char
            continue

        if char == open_char:
            depth += 1
            continue

        if char == close_char:
            depth -= 1
            if depth == 0:
                return "".join(segment_chars), index + 1

    return None, start_index


def _extract_action_matches(text: str) -> list[tuple[str, str, str]]:
    matches: list[tuple[str, str, str]] = []
    upper_text = text.upper()
    token = ACTION_TOKEN.upper()
    cursor = 0

    while True:
        start = upper_text.find(token, cursor)
        if start == -1:
            break

        index = start + len(token)
        while index < len(text) and text[index].isspace():
            index += 1

        type_start = index
        while index < len(text) and (text[index].isalnum() or text[index] == "_"):
            index += 1

        if index == type_start:
            cursor = start + len(token)
            continue

        action_type = text[type_start:index]

        while index < len(text) and text[index].isspace():
            index += 1

        params_str = ""
        if index < len(text) and text[index] in {"(", "{"}:
            open_char = text[index]
            close_char = ")" if open_char == "(" else "}"
            segment, next_index = _read_balanced_segment(text, index, open_char, close_char)
            if segment is None:
                cursor = start + len(token)
                continue
            params_str = segment[1:-1].strip()
            index = next_index

        while index < len(text) and text[index].isspace():
            index += 1

        if index >= len(text) or text[index] != "]":
            cursor = start + len(token)
            continue

        raw_action = text[start : index + 1]
        matches.append((action_type, params_str, raw_action))
        cursor = index + 1

    return matches


class ActionPlanner:
    """
    Planificador de acciones.
    - Parsea la respuesta del LLM buscando acciones
    - Ejecuta acciones en el PC o dispositivo Android
    - Proporciona feedback del resultado
    """

    def __init__(
        self,
        automation: AutomationEngine,
        adb: ADBController,
        vision: VisionEngine,
        browser: BrowserController | None = None,
        terminals: TerminalManager | None = None,
        workspace: WorkspaceManager | None = None,
        ide: IDEManager | None = None,
        editor_bridge: EditorBridge | None = None,
        skill_registry: SkillRegistry | None = None,
        mcp_registry: MCPRegistry | None = None,
        payment_registry: PaymentRegistry | None = None,
        skill_runtime: SkillRuntime | None = None,
        mcp_runtime: MCPRuntime | None = None,
    ):
        self._auto = automation
        self._adb = adb
        self._vision = vision
        self._browser = browser
        self._terminals = terminals
        self._workspace = workspace
        self._ide = ide
        self._editor_bridge = editor_bridge
        self._browseruse: object | None = None  # Lazy BrowserUseBridge fallback
        self._skills = skill_registry or SkillRegistry()
        self._mcp = mcp_registry or get_mcp_registry()
        self._payments = payment_registry or PaymentRegistry()
        self._skill_runtime = skill_runtime or SkillRuntime(self._skills)
        self._mcp_runtime = mcp_runtime or MCPRuntime(self._mcp)
        # Screen dimensions tracking for coordinate scaling
        self._screen_dims: dict[str, int] | None = None

    def parse_actions(self, llm_text: str) -> list[Action]:
        """
        Extrae acciones del texto del LLM.
        Formato reconocido:
            [ACTION:click(x=500, y=300)]
            [ACTION:type(text=Hola mundo)]
            [ACTION:hotkey(keys=ctrl+c)]
            [ACTION:screenshot()]
            [ACTION:scroll(clicks=-3)]
            [ACTION:wait(seconds=2)]
        """
        actions = []

        for raw_action_type, params_str, raw_description in _extract_action_matches(llm_text):
            action_type = self._normalize_action_type(raw_action_type)

            params = self._normalize_action_params(action_type, self._parse_params(params_str))
            actions.append(Action(
                type=action_type,
                params=params,
                description=raw_description,
            ))

        # También intentar parsear JSON tool calls
        json_actions = self._parse_json_actions(llm_text)
        actions.extend(json_actions)

        if actions:
            logger.info("=== ACTIONS PARSED START ===")
            for index, action in enumerate(actions, start=1):
                logger.info(
                    f"[ACTION {index}] type={action.type} params={action.params} raw={action.description}"
                )
            logger.info("=== ACTIONS PARSED END ===")
        else:
            logger.info("=== ACTIONS PARSED: none ===")
            if "[ACTION:" in llm_text or "ACTION:" in llm_text:
                import re as _re_diag
                bracket_matches = _re_diag.findall(r'\[ACTION:[^\]]{0,300}', llm_text)
                loose_matches = _re_diag.findall(r'\bACTION\s*:\s*\S+[^\n]{0,200}', llm_text, flags=_re_diag.IGNORECASE)
                logger.warning(
                    f"parse_actions: text contains ACTION patterns but none parsed! "
                    f"text_len={len(llm_text)}, "
                    f"bracket_patterns={bracket_matches[:5]}, "
                    f"loose_patterns={loose_matches[:5]}"
                )
                logger.trace(
                    f"parse_actions FULL TEXT for failed parse:\n"
                    f"--- PARSE INPUT START ---\n{llm_text}\n--- PARSE INPUT END ---"
                )

        return actions

    def _parse_params(self, params_str: str) -> dict[str, Any]:
        """Parsea parámetros en formato key=value, manejando arrays y selectores CSS."""
        params: dict[str, Any] = {}
        if not params_str:
            return params

        raw_segments = _split_top_level_segments(params_str)
        processed: list[str] = []
        current_pair = ""

        for segment in raw_segments:
            if _find_top_level_equals(segment) != -1:
                if current_pair:
                    processed.append(current_pair.strip())
                current_pair = segment.strip()
            elif current_pair:
                current_pair = f"{current_pair}, {segment.strip()}"
            else:
                current_pair = segment.strip()

        if current_pair:
            processed.append(current_pair.strip())

        for pair in processed:
            eq_index = _find_top_level_equals(pair)
            if eq_index == -1:
                continue

            key = pair[:eq_index].strip()
            value: Any = _strip_wrapping_quotes(pair[eq_index + 1 :].strip())
            value = value.replace('\\"', '"').replace("\\'", "'")
            lowered_key = key.lower()

            if (
                isinstance(value, str)
                and ((value.startswith("[") and value.endswith("]")) or (value.startswith("{") and value.endswith("}")))
            ):
                try:
                    parsed_json = json.loads(value)
                    if isinstance(parsed_json, list):
                        if lowered_key in {"coordinates", "point", "position", "coords"} and len(parsed_json) >= 2:
                            params["x"] = int(parsed_json[0])
                            params["y"] = int(parsed_json[1])
                        else:
                            params[key] = parsed_json
                        continue
                    if isinstance(parsed_json, dict):
                        params[key] = parsed_json
                        continue
                except (json.JSONDecodeError, ValueError):
                    pass

            if lowered_key in {
                "submit",
                "clear",
                "new_window",
                "headless",
                "force",
                "append",
                "recursive",
                "include_hidden",
                "include_dirs",
                "include_git",
                "case_sensitive",
                "save",
                "preserve_focus",
            }:
                params[key] = _coerce_bool(value)
                continue

            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass

            params[key] = value

        return params

    def _parse_json_actions(self, text: str) -> list[Action]:
        """Intenta parsear acciones en formato JSON del LLM."""
        actions = []

        # Buscar bloques JSON con tool_calls o actions
        json_pattern = re.compile(r'```json\s*([\s\S]*?)```')
        for match in json_pattern.finditer(text):
            try:
                data = json.loads(match.group(1))
                if isinstance(data, list):
                    for item in data:
                        if "action" in item or "type" in item:
                            action_type = self._normalize_action_type(item.get("action", item.get("type", "")))
                            actions.append(Action(
                                type=action_type,
                                params=self._normalize_action_params(
                                    action_type,
                                    dict(item.get("params", item.get("parameters", {})) or {}),
                                ),
                                description=str(item),
                            ))
                elif isinstance(data, dict):
                    if "action" in data or "type" in data:
                        action_type = self._normalize_action_type(data.get("action", data.get("type", "")))
                        actions.append(Action(
                            type=action_type,
                            params=self._normalize_action_params(
                                action_type,
                                dict(data.get("params", data.get("parameters", {})) or {}),
                            ),
                            description=str(data),
                        ))
            except json.JSONDecodeError:
                continue

        return actions

    def _normalize_action_type(self, action_type: str) -> str:
        normalized = str(action_type or "").strip().lower()
        return ACTION_TYPE_ALIASES.get(normalized, normalized)

    def _normalize_action_params(self, action_type: str, params: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(params)
        for bool_key in {
            "submit",
            "clear",
            "new_window",
            "headless",
            "force",
            "append",
            "recursive",
            "include_hidden",
            "include_dirs",
            "include_git",
            "case_sensitive",
            "save",
            "preserve_focus",
        }:
            if bool_key in normalized:
                normalized[bool_key] = _coerce_bool(normalized[bool_key], default=bool(normalized[bool_key]))

        if action_type in {"browser_fill", "browser_type"} and "text" not in normalized and "value" in normalized:
            normalized["text"] = normalized["value"]

        if action_type == "browser_extract" and "selector" not in normalized:
            normalized["selector"] = "body"

        if action_type == "browser_use_automation_profile" and "profile_name" not in normalized and "profile_id" in normalized:
            normalized["profile_name"] = normalized["profile_id"]

        for json_key in {"payload", "input", "arguments"}:
            raw_value = normalized.get(json_key)
            if isinstance(raw_value, str):
                candidate = raw_value.strip()
                if candidate.startswith("{") or candidate.startswith("["):
                    try:
                        normalized[json_key] = json.loads(candidate)
                    except json.JSONDecodeError:
                        pass

        if action_type == "file_write_text":
            if "text" not in normalized and "content" in normalized:
                normalized["text"] = normalized["content"]
            if "path" not in normalized:
                for alias in ("file_path", "filepath", "target_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break

        if action_type == "file_exists" and "path" not in normalized:
            for alias in ("file_path", "filepath", "target_path"):
                if alias in normalized:
                    normalized["path"] = normalized[alias]
                    break

        if action_type == "file_list":
            if "path" not in normalized:
                for alias in ("dir", "directory", "folder", "base_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "pattern" not in normalized and "glob" in normalized:
                normalized["pattern"] = normalized["glob"]
            if "max_results" not in normalized and "limit" in normalized:
                normalized["max_results"] = normalized["limit"]

        if action_type == "file_read_text":
            if "path" not in normalized:
                for alias in ("file_path", "filepath", "target_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "start_line" not in normalized:
                for alias in ("line", "from_line", "line_start"):
                    if alias in normalized:
                        normalized["start_line"] = normalized[alias]
                        break
            if "max_lines" not in normalized and "lines" in normalized:
                normalized["max_lines"] = normalized["lines"]
            if "max_chars" not in normalized and "chars" in normalized:
                normalized["max_chars"] = normalized["chars"]

        if action_type == "file_read_batch":
            if "paths" not in normalized:
                for alias in ("files", "file_paths"):
                    if alias in normalized:
                        normalized["paths"] = normalized[alias]
                        break
            if "max_chars_per_file" not in normalized and "max_chars" in normalized:
                normalized["max_chars_per_file"] = normalized["max_chars"]

        if action_type == "file_search_text":
            if "path" not in normalized:
                for alias in ("dir", "directory", "folder", "base_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "query" not in normalized:
                for alias in ("text", "search", "needle"):
                    if alias in normalized:
                        normalized["query"] = normalized[alias]
                        break
            if "pattern" not in normalized and "glob" in normalized:
                normalized["pattern"] = normalized["glob"]
            if "max_results" not in normalized and "limit" in normalized:
                normalized["max_results"] = normalized["limit"]

        if action_type == "file_replace_text":
            if "path" not in normalized:
                for alias in ("file_path", "filepath", "target_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "find" not in normalized:
                for alias in ("search", "old", "needle"):
                    if alias in normalized:
                        normalized["find"] = normalized[alias]
                        break
            if "replace" not in normalized:
                for alias in ("replacement", "new"):
                    if alias in normalized:
                        normalized["replace"] = normalized[alias]
                        break

        if action_type == "workspace_snapshot":
            if "path" not in normalized:
                for alias in ("dir", "directory", "folder", "base_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "max_entries" not in normalized and "limit" in normalized:
                normalized["max_entries"] = normalized["limit"]

        if action_type == "git_status":
            if "path" not in normalized:
                for alias in ("dir", "directory", "folder", "base_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "max_entries" not in normalized and "limit" in normalized:
                normalized["max_entries"] = normalized["limit"]

        if action_type == "git_changed_files":
            if "path" not in normalized:
                for alias in ("dir", "directory", "folder", "base_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "max_entries" not in normalized and "limit" in normalized:
                normalized["max_entries"] = normalized["limit"]

        if action_type == "git_diff":
            if "path" not in normalized:
                for alias in ("file_path", "filepath", "target_path", "dir", "directory", "folder", "base_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "max_chars" not in normalized and "chars" in normalized:
                normalized["max_chars"] = normalized["chars"]

        if action_type == "git_log":
            if "path" not in normalized:
                for alias in ("file_path", "filepath", "target_path", "dir", "directory", "folder", "base_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "limit" not in normalized and "max_entries" in normalized:
                normalized["limit"] = normalized["max_entries"]

        if action_type in {"code_outline", "code_related_files"}:
            if "path" not in normalized:
                for alias in ("file_path", "filepath", "target_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "max_results" not in normalized and "limit" in normalized:
                normalized["max_results"] = normalized["limit"]
            if "max_symbols" not in normalized and "limit" in normalized and action_type == "code_outline":
                normalized["max_symbols"] = normalized["limit"]

        if action_type == "ide_open_workspace":
            if "path" not in normalized:
                for alias in ("dir", "directory", "folder", "base_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "editor_key" not in normalized and "editor" in normalized:
                normalized["editor_key"] = normalized["editor"]

        if action_type == "ide_open_file":
            if "path" not in normalized:
                for alias in ("file_path", "filepath", "target_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "line" not in normalized and "start_line" in normalized:
                normalized["line"] = normalized["start_line"]
            if "editor_key" not in normalized and "editor" in normalized:
                normalized["editor_key"] = normalized["editor"]

        if action_type == "ide_open_diff":
            if "left_path" not in normalized:
                for alias in ("path_left", "from_path", "old_path"):
                    if alias in normalized:
                        normalized["left_path"] = normalized[alias]
                        break
            if "right_path" not in normalized:
                for alias in ("path_right", "to_path", "new_path"):
                    if alias in normalized:
                        normalized["right_path"] = normalized[alias]
                        break
            if "editor_key" not in normalized and "editor" in normalized:
                normalized["editor_key"] = normalized["editor"]

        if action_type == "ide_diagnostics" and "path" not in normalized:
            for alias in ("file_path", "filepath", "target_path"):
                if alias in normalized:
                    normalized["path"] = normalized[alias]
                    break

        if action_type == "ide_symbols" and "path" not in normalized:
            for alias in ("file_path", "filepath", "target_path"):
                if alias in normalized:
                    normalized["path"] = normalized[alias]
                    break
            if "max_symbols" not in normalized and "limit" in normalized:
                normalized["max_symbols"] = normalized["limit"]

        if action_type == "ide_find_symbol":
            if "path" not in normalized:
                for alias in ("file_path", "filepath", "target_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "query" not in normalized:
                for alias in ("symbol", "name", "search", "needle", "text"):
                    if alias in normalized:
                        normalized["query"] = normalized[alias]
                        break
            if "kind" not in normalized and "symbol_kind" in normalized:
                normalized["kind"] = normalized["symbol_kind"]
            if "max_results" not in normalized and "limit" in normalized:
                normalized["max_results"] = normalized["limit"]

        if action_type == "ide_reveal_symbol":
            if "path" not in normalized:
                for alias in ("file_path", "filepath", "target_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "query" not in normalized:
                for alias in ("symbol", "name", "search", "needle", "text"):
                    if alias in normalized:
                        normalized["query"] = normalized[alias]
                        break
            if "kind" not in normalized and "symbol_kind" in normalized:
                normalized["kind"] = normalized["symbol_kind"]
            if "occurrence" not in normalized:
                for alias in ("index", "result_index", "match_index"):
                    if alias in normalized:
                        normalized["occurrence"] = normalized[alias]
                        break

        if action_type in {"ide_open_diagnostic", "ide_next_diagnostic", "ide_prev_diagnostic"}:
            if "path" not in normalized:
                for alias in ("file_path", "filepath", "target_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "index" not in normalized:
                for alias in ("problem_index", "diagnostic_index", "issue_index"):
                    if alias in normalized:
                        normalized["index"] = normalized[alias]
                        break

        if action_type == "ide_reveal_range":
            if "path" not in normalized:
                for alias in ("file_path", "filepath", "target_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "start_line" not in normalized:
                for alias in ("line", "from_line", "line_start"):
                    if alias in normalized:
                        normalized["start_line"] = normalized[alias]
                        break
            if "start_column" not in normalized:
                for alias in ("column", "col", "from_column", "column_start"):
                    if alias in normalized:
                        normalized["start_column"] = normalized[alias]
                        break
            if "end_line" not in normalized:
                for alias in ("to_line", "line_end"):
                    if alias in normalized:
                        normalized["end_line"] = normalized[alias]
                        break
            if "end_column" not in normalized:
                for alias in ("to_column", "column_end", "col_end"):
                    if alias in normalized:
                        normalized["end_column"] = normalized[alias]
                        break

        if action_type == "ide_apply_edit":
            if "path" not in normalized:
                for alias in ("file_path", "filepath", "target_path"):
                    if alias in normalized:
                        normalized["path"] = normalized[alias]
                        break
            if "text" not in normalized:
                for alias in ("content", "value", "replacement"):
                    if alias in normalized:
                        normalized["text"] = normalized[alias]
                        break
            if "start_line" not in normalized:
                for alias in ("line", "from_line", "line_start"):
                    if alias in normalized:
                        normalized["start_line"] = normalized[alias]
                        break
            if "start_column" not in normalized:
                for alias in ("column", "col", "from_column", "column_start"):
                    if alias in normalized:
                        normalized["start_column"] = normalized[alias]
                        break
            if "end_line" not in normalized:
                for alias in ("to_line", "line_end"):
                    if alias in normalized:
                        normalized["end_line"] = normalized[alias]
                        break
            if "end_column" not in normalized:
                for alias in ("to_column", "column_end", "col_end"):
                    if alias in normalized:
                        normalized["end_column"] = normalized[alias]
                        break

        if action_type == "ide_apply_workspace_edits":
            if "edits" not in normalized:
                for alias in ("changes", "operations", "workspace_edits"):
                    if alias in normalized:
                        normalized["edits"] = normalized[alias]
                        break

        return normalized

    def _get_retry_policy(self, action_type: str) -> RetryPolicy:
        max_attempts = 1
        if action_type == "screenshot":
            max_attempts = int(config.get("vision", "capture_retry_attempts", default=3))
        elif action_type in {
            "click",
            "double_click",
            "right_click",
            "type",
            "focus_type",
            "press",
            "key",
            "hotkey",
            "scroll",
            "move",
            "drag",
        } or action_type.startswith("browser_"):
            max_attempts = int(config.get("automation", "action_retry_attempts", default=2))

        return RetryPolicy(
            max_attempts=max_attempts,
            initial_delay_ms=int(config.get("automation", "retry_initial_delay_ms", default=300)),
            backoff_multiplier=float(config.get("automation", "retry_backoff_multiplier", default=2.0)),
            max_delay_ms=int(config.get("automation", "retry_max_delay_ms", default=2000)),
        )

    def _classify_result_failure(self, action: Action, result: dict[str, Any]) -> tuple[str | None, bool]:
        if result.get("success"):
            return None, False

        message = _normalize_match_text(result.get("message", ""))
        if "coordenadas invalidas" in message:
            return "validation", False
        if (
            "no esta conectada" in message
            or ("chrome" in message and "conect" in message)
            or ("extension" in message and "conect" in message)
            or "no conecto" in message
            or "backend estructurado de navegador" in message
            or "backend de browser automatizado" in message
        ):
            return "dependency_unavailable", False

        if "coordenadas inválidas" in message or "falta " in message:
            return "validation", False
        if "bloqueada" in message or "configuracion del usuario" in message or "exec approval" in message:
            return "policy", False
        if "no disponible" in message:
            return "dependency_unavailable", False
        if action.type == "screenshot":
            return "vision_capture_failed", True
        if action.type.startswith("browser_"):
            return "browser_action_failed", True
        if action.type in {"click", "double_click", "right_click", "type", "focus_type", "press", "key", "hotkey", "scroll", "move", "drag"}:
            return "desktop_action_failed", True
        return "action_failed", False

    async def _attempt_recovery(self, action: Action, failure_kind: str | None) -> None:
        if action.type == "screenshot":
            logger.warning("Intentando recuperar VisionEngine tras fallo de captura")
            await self._vision.initialize()
            return

        if action.type.startswith("browser_"):
            await asyncio.sleep(0.25)
            return

        if failure_kind == "desktop_action_failed":
            await asyncio.sleep(0.15)

    def _normalize_hotkey_keys(self, raw_keys: Any) -> list[str]:
        if isinstance(raw_keys, list):
            return [str(key).strip() for key in raw_keys if str(key).strip()]
        keys_str = str(raw_keys or "").strip()
        if not keys_str:
            return []
        if keys_str.startswith("[") and keys_str.endswith("]"):
            try:
                parsed = json.loads(keys_str)
                if isinstance(parsed, list):
                    return [str(key).strip() for key in parsed if str(key).strip()]
            except json.JSONDecodeError:
                pass
        return [key.strip() for key in keys_str.split("+") if key.strip()]

    def _extract_filename_candidates(self, text: str) -> list[str]:
        quoted_matches = re.findall(r'["\']([^"\']+\.[A-Za-z0-9]{1,8})["\']', text or "")
        bare_matches = re.findall(r"(?<![\\/])([A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,8})", text or "")
        matches = quoted_matches + bare_matches
        seen: set[str] = set()
        candidates: list[str] = []
        for match in matches:
            cleaned = match.strip().strip("'\"").rstrip(".,;:)")
            lowered = cleaned.lower()
            if cleaned and lowered not in seen:
                seen.add(lowered)
                candidates.append(cleaned)
        return candidates

    def _find_existing_file_candidate(self, filename: str) -> Path | None:
        raw_text = str(filename or "").strip().strip("\"'")
        if not raw_text:
            return None

        if "\\" in raw_text or "/" in raw_text:
            try:
                resolved = self._workspace.resolve_path(raw_text) if self._workspace else self._auto.resolve_local_path(raw_text)
            except Exception:
                resolved = Path(raw_text).expanduser()
            if resolved.exists():
                return resolved

        home = Path.home()
        workspace_root = self._workspace.root_dir if self._workspace else Path.cwd()
        search_roots = [
            workspace_root,
            home / "Desktop",
            home / "Downloads",
            home / "Documents",
            home,
            Path.cwd(),
        ]
        for root in search_roots:
            candidate = root / raw_text
            if candidate.exists():
                return candidate
        return None

    def _infer_local_file_recovery(
        self,
        filename_candidates: list[str],
        prior_actions: list[Action],
    ) -> dict[str, str] | None:
        if not filename_candidates:
            return None

        filename = filename_candidates[0]
        preferred_path = str(Path.home() / "Desktop" / filename)
        typed_text_candidates: list[str] = []

        for prior_action in prior_actions:
            if prior_action.type != "type":
                continue
            raw_text = str(prior_action.params.get("text", "")).strip()
            if not raw_text:
                continue
            lowered = raw_text.lower()
            if lowered in {"notepad", filename.lower()}:
                continue
            if raw_text.startswith("$HOME") or raw_text.startswith("~"):
                continue
            if "\\" in raw_text or "/" in raw_text:
                continue
            if self._extract_filename_candidates(raw_text):
                continue
            typed_text_candidates.append(raw_text)

        if not typed_text_candidates:
            return {"path": preferred_path}

        typed_text_candidates.sort(key=len, reverse=True)
        return {
            "path": preferred_path,
            "text": typed_text_candidates[0],
        }

    def _validate_task_completion(
        self,
        action: Action,
        result: dict[str, Any],
        prior_results: list[dict[str, Any]],
        prior_actions: list[Action],
    ) -> dict[str, Any]:
        failures = [item for item in prior_results if not item.get("success")]
        if failures:
            failed_actions = ", ".join(item.get("action", "?") for item in failures[:4])
            result["success"] = False
            result["task_complete"] = False
            result["message"] = (
                "No puedo confirmar la tarea porque hubo acciones fallidas antes de cerrarla: "
                f"{failed_actions}"
            )
            return result

        save_related = False
        for prior_action in prior_actions:
            if prior_action.type in {
                "browser_download_click",
                "browser_check_downloads",
                "downloads_check",
                "file_write_text",
                "file_exists",
                "file_replace_text",
            }:
                save_related = True
                break
            if prior_action.type == "hotkey":
                normalized_keys = [key.lower() for key in self._normalize_hotkey_keys(prior_action.params.get("keys"))]
                if ("ctrl" in normalized_keys or "control" in normalized_keys) and "s" in normalized_keys:
                    save_related = True
                    break

        if not save_related:
            return result

        summary = str(action.params.get("summary", ""))
        candidates = self._extract_filename_candidates(summary)
        if not candidates:
            for prior_action in prior_actions:
                if prior_action.type == "type":
                    candidates.extend(self._extract_filename_candidates(str(prior_action.params.get("text", ""))))
                elif prior_action.type in {"file_write_text", "file_exists", "file_replace_text"}:
                    raw_path = str(prior_action.params.get("path", "")).strip()
                    if raw_path:
                        candidates.append(raw_path)
                        candidates.append(Path(raw_path).name)

        unique_candidates: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            lowered = candidate.lower()
            if lowered not in seen:
                seen.add(lowered)
                unique_candidates.append(candidate)

        if not unique_candidates:
            result["success"] = False
            result["task_complete"] = False
            result["message"] = (
                "No puedo confirmar la tarea porque hubo una accion de guardado o descarga, "
                "pero no se identifico ningun archivo para verificar."
            )
            return result

        found_paths = [self._find_existing_file_candidate(candidate) for candidate in unique_candidates]
        found_paths = [path for path in found_paths if path is not None]
        if not found_paths:
            recovery_hint = self._infer_local_file_recovery(unique_candidates, prior_actions)
            if recovery_hint:
                result["data"] = {
                    **(result.get("data") or {}),
                    "recovery_hint": recovery_hint,
                }
            result["success"] = False
            result["task_complete"] = False
            result["message"] = (
                "No puedo confirmar la tarea porque no encontre en disco el archivo esperado: "
                + ", ".join(unique_candidates[:3])
            )
            return result

        result["message"] = (
            f"{result.get('message', '')} Verificado en disco: "
            + ", ".join(str(path) for path in found_paths[:3])
        ).strip()
        return result

    async def _execute_with_resilience(self, action: Action) -> dict[str, Any]:
        policy = self._get_retry_policy(action.type)
        started_at = time.perf_counter()
        attempt_log: list[ActionAttemptRecord] = []
        last_result: dict[str, Any] | None = None

        for attempt in range(1, policy.max_attempts + 1):
            attempt_started = time.perf_counter()
            try:
                current_result = await self._execute_single(action)
            except Exception as exc:
                duration_ms = (time.perf_counter() - attempt_started) * 1000
                attempt_log.append(
                    ActionAttemptRecord(
                        attempt=attempt,
                        success=False,
                        duration_ms=duration_ms,
                        message=str(exc),
                        error_kind="exception",
                    )
                )
                if attempt >= policy.max_attempts:
                    result_model = ActionExecutionResultModel(
                        action=action.type,
                        success=False,
                        message=f"Error ejecutando acción: {exc}",
                        attempts=attempt,
                        retry_count=max(0, attempt - 1),
                        failure_kind="exception",
                        recoverable=False,
                        duration_ms=(time.perf_counter() - started_at) * 1000,
                        attempt_log=attempt_log,
                    )
                    return result_model.model_dump(exclude_none=True)

                delay = policy.delay_seconds(attempt)
                logger.warning(
                    f"Acción {action.type} lanzó excepción (intento {attempt}/{policy.max_attempts}): {exc}. "
                    f"Reintentando en {delay:.2f}s"
                )
                await self._attempt_recovery(action, "exception")
                await asyncio.sleep(delay)
                continue

            last_result = current_result
            failure_kind, retryable = self._classify_result_failure(action, current_result)
            duration_ms = (time.perf_counter() - attempt_started) * 1000
            attempt_log.append(
                ActionAttemptRecord(
                    attempt=attempt,
                    success=bool(current_result.get("success")),
                    duration_ms=duration_ms,
                    message=str(current_result.get("message", "")),
                    error_kind=failure_kind,
                )
            )

            if current_result.get("success") or not retryable or attempt >= policy.max_attempts:
                result_model = ActionExecutionResultModel(
                    action=action.type,
                    success=bool(current_result.get("success")),
                    message=str(current_result.get("message", "")),
                    data=current_result.get("data"),
                    task_complete=bool(current_result.get("task_complete", False)),
                    attempts=attempt,
                    retry_count=max(0, attempt - 1),
                    failure_kind=failure_kind,
                    recoverable=retryable,
                    duration_ms=(time.perf_counter() - started_at) * 1000,
                    attempt_log=attempt_log,
                )
                return result_model.model_dump(exclude_none=True)

            delay = policy.delay_seconds(attempt)
            logger.warning(
                f"Acción {action.type} falló sin progreso observable "
                f"(intento {attempt}/{policy.max_attempts}, kind={failure_kind}). "
                f"Reintentando en {delay:.2f}s"
            )
            await self._attempt_recovery(action, failure_kind)
            await asyncio.sleep(delay)

        result_model = ActionExecutionResultModel(
            action=action.type,
            success=bool(last_result and last_result.get("success")),
            message=str((last_result or {}).get("message", "La acción no produjo resultado")),
            data=(last_result or {}).get("data"),
            task_complete=bool((last_result or {}).get("task_complete", False)),
            attempts=max(1, len(attempt_log)),
            retry_count=max(0, len(attempt_log) - 1),
            failure_kind="unknown",
            recoverable=False,
            duration_ms=(time.perf_counter() - started_at) * 1000,
            attempt_log=attempt_log,
        )
        return result_model.model_dump(exclude_none=True)

    async def execute_actions(self, actions: list[Action]) -> list[dict[str, Any]]:
        """
        Ejecuta una lista de acciones y retorna resultados.
        """
        results = []
        batch_stamp = int(time.time() * 1000)

        for index, action in enumerate(actions):
            logger.info(f"Ejecutando acción: {action.type} — {action.params}")

            # Ciclo de vida de la tarjeta de accion (frontend):
            # START crea la tarjeta con su SVG y arranca el contador;
            # RESULT la cierra (OK/ERROR), detiene el contador y guarda la duracion.
            action_id = f"act_{batch_stamp}_{index}"
            await _emit_action_start(action_id, action.type, action.params)

            result = await self._execute_with_resilience(action)
            if action.type == "task_complete":
                result = self._validate_task_completion(
                    action,
                    result,
                    prior_results=results,
                    prior_actions=actions[:index],
                )
            results.append(result)
            logger.info(
                f"[ACTION RESULT] type={result['action']} success={result['success']} "
                f"message={result.get('message', '')} attempts={result.get('attempts', 1)}"
            )

            await _emit_action_result(
                action_id,
                action.type,
                bool(result.get("success")),
                str(result.get("message", "")),
                result.get("duration_ms"),
            )

            if not result.get("success") and action.type != "task_complete":
                logger.warning(
                    f"Abortando el lote actual tras fallo de {action.type}. "
                    "Se replanificara con el feedback acumulado."
                )
                break

            # Pausa entre acciones para estabilidad visual
            if action.type != "wait":
                await asyncio.sleep(max(0.05, int(config.get("automation", "action_delay_ms", default=200)) / 1000.0))

        logger.info("=== ACTION EXECUTION SUMMARY START ===")
        for result in results:
            logger.info(
                f"{result['action']} | success={result['success']} | "
                f"attempts={result.get('attempts', 1)} | "
                f"failure_kind={result.get('failure_kind', '')} | "
                f"message={result.get('message', '')}"
            )
        logger.info("=== ACTION EXECUTION SUMMARY END ===")

        return results

    def _scale_coordinates(self, x: int, y: int) -> tuple[int, int]:
        """
        Escala coordenadas del LLM (basadas en la imagen enviada) a coordenadas de pyautogui (lógicas).

        El flujo completo es:
        1. mss captura en píxeles FÍSICOS (ej: 1920×1080)
        2. La imagen se redimensiona a sent_w × sent_h (ej: 1280×720) para ahorrar tokens
        3. El LLM ve la imagen de sent_w × sent_h y devuelve coordenadas en ese espacio
        4. pyautogui opera en coordenadas LÓGICAS (ej: 1280×720 si DPI=150%, o 1920×1080 si DPI=100%)
        5. Necesitamos mapear: LLM coords (sent space) → pyautogui coords (logical space)

        Fórmula: real_x = llm_x * (logical_w / sent_w)
                 real_y = llm_y * (logical_h / sent_h)
        """
        if not self._screen_dims:
            return x, y

        logical_w = self._screen_dims.get("logical_w", 0)
        logical_h = self._screen_dims.get("logical_h", 0)
        sent_w = self._screen_dims.get("sent_w", 0)
        sent_h = self._screen_dims.get("sent_h", 0)

        if sent_w <= 0 or sent_h <= 0 or logical_w <= 0 or logical_h <= 0:
            return x, y

        if sent_w == logical_w and sent_h == logical_h:
            return x, y  # No se necesita escalar

        scale_x = logical_w / sent_w
        scale_y = logical_h / sent_h
        new_x = int(round(x * scale_x))
        new_y = int(round(y * scale_y))

        # Clamp a los límites de la pantalla lógica
        new_x = max(0, min(new_x, logical_w - 1))
        new_y = max(0, min(new_y, logical_h - 1))

        if new_x != x or new_y != y:
            logger.debug(
                f"Coordenadas escaladas: ({x}, {y}) → ({new_x}, {new_y}) "
                f"[sent={sent_w}x{sent_h} → logical={logical_w}x{logical_h}, "
                f"scale={scale_x:.3f}x{scale_y:.3f}]"
            )

        return new_x, new_y

    async def _ensure_browser_session(self) -> bool:
        """Auto-inicializa sesión de browser si no hay una activa. Retorna True si listo.
        Checks extension connection first to avoid useless initialization attempts."""
        if not self._browser or not self._browser.is_available():
            return False
        # If already connected via extension, reuse
        if self._browser._mode is not None:
            # Verify extension is still actually connected
            ext = getattr(self._browser, '_ext', None)
            if ext and hasattr(ext, 'is_connected') and not ext.is_connected:
                logger.info("[Planner] Extension disconnected, clearing stale browser session")
                self._browser._mode = None
                return False
            return True
        # Check if extension is connected before trying to init
        ext = getattr(self._browser, '_ext', None)
        if ext and hasattr(ext, 'is_connected') and not ext.is_connected:
            logger.debug("[Planner] Extension not connected, skipping browser session init")
            return False
        try:
            logger.info("[Planner] Auto-init: inicializando sesión de browser (human profile)...")
            await self._browser.ensure_human_profile()
            return self._browser._mode is not None
        except Exception as e:
            logger.warning(f"[Planner] Auto-init browser falló: {e}")
            return False

    async def _get_browseruse(self):
        """Lazy-init BrowserUseBridge como fallback cuando la extensión no está conectada."""
        if self._browseruse is not None:
            return self._browseruse
        try:
            from backend.automation.browseruse_bridge import BrowserUseBridge
            bridge = BrowserUseBridge()
            await bridge.initialize()
            if not bridge.is_available():
                logger.info("[Planner] browser-use no disponible (no instalado)")
                return None
            await bridge.ensure_automation_profile("g-mini-auto")
            self._browseruse = bridge
            logger.info("[Planner] BrowserUseBridge inicializado como fallback")
            return bridge
        except Exception as e:
            logger.warning(f"[Planner] BrowserUseBridge fallback falló: {e}")
            return None

    async def _execute_single(self, action: Action) -> dict[str, Any]:
        """Ejecuta una sola acción."""
        result = {
            "action": action.type,
            "success": False,
            "message": "",
        }

        # Computer use nativo deshabilitado en el agente principal (coordinador).
        if action.type in _BLOCKED_MAIN_DESKTOP_ACTIONS:
            logger.warning(f"Acción de escritorio nativa bloqueada en el agente principal: {action.type}")
            result["message"] = (
                "Acción de escritorio directa deshabilitada en el agente principal. "
                "Usa MCPControl (mcp_call_tool con server_id='mcpcontrol') si está disponible, "
                "o delega con [ACTION:delegate_computer_use(task=...)]."
            )
            return result

        try:
            match action.type:
                case "delegate_computer_use":
                    task = str(action.params.get("task", "")).strip()
                    monitor = int(action.params.get("monitor", 0) or 0)
                    if not task:
                        result["message"] = "Falta 'task' en delegate_computer_use"
                        return result
                    result["success"] = True
                    result["data"] = {"task": task, "monitor": monitor, "delegated": True}
                    result["message"] = f"Delegacion computer use: {task[:100]}"

                case "click":
                    x = int(action.params.get("x", 0))
                    y = int(action.params.get("y", 0))
                    button = str(action.params.get("button", "left"))
                    # Escalar coordenadas si el screenshot fue redimensionado
                    x, y = self._scale_coordinates(x, y)
                    # Validar coordenadas - evitar fail-safe de PyAutoGUI
                    if x < 10 or y < 10:
                        result["message"] = f"Coordenadas inválidas ({x}, {y}) - muy cerca de la esquina"
                        return result
                    # Emitir evento visual ANTES del click
                    await _emit_action_event("click", {"x": x, "y": y, "button": button})
                    ok = await self._auto.click(x, y, button=button)
                    result["success"] = ok
                    result["message"] = f"Click en ({x}, {y})" if ok else "Click falló"

                case "double_click":
                    x = int(action.params.get("x", 0))
                    y = int(action.params.get("y", 0))
                    x, y = self._scale_coordinates(x, y)
                    if x < 10 or y < 10:
                        result["message"] = f"Coordenadas inválidas ({x}, {y})"
                        return result
                    await _emit_action_event("double_click", {"x": x, "y": y})
                    ok = await self._auto.double_click(x, y)
                    result["success"] = ok
                    result["message"] = f"Doble click en ({x}, {y})" if ok else "Double click falló"

                case "right_click":
                    x = int(action.params.get("x", 0))
                    y = int(action.params.get("y", 0))
                    x, y = self._scale_coordinates(x, y)
                    if x < 10 or y < 10:
                        result["message"] = f"Coordenadas inválidas ({x}, {y})"
                        return result
                    await _emit_action_event("right_click", {"x": x, "y": y})
                    ok = await self._auto.right_click(x, y)
                    result["success"] = ok
                    result["message"] = f"Click derecho en ({x}, {y})" if ok else "Click derecho falló"

                case "type":
                    text = str(action.params.get("text", ""))
                    await _emit_action_event("type", {"text": text})
                    ok = await self._auto.write_text(text)
                    submit = _coerce_bool(action.params.get("submit", False))
                    if ok and submit:
                        await self._auto.press_key("enter")
                    result["success"] = ok
                    result["message"] = f"Texto escrito: {text[:50]}"

                case "focus_type":
                    x = int(action.params.get("x", 0))
                    y = int(action.params.get("y", 0))
                    x, y = self._scale_coordinates(x, y)
                    text = str(action.params.get("text", ""))
                    clear = _coerce_bool(action.params.get("clear", True), default=True)
                    submit = _coerce_bool(action.params.get("submit", False))
                    if x < 10 or y < 10:
                        result["message"] = f"Coordenadas inválidas ({x}, {y})"
                        return result
                    await _emit_action_event("click", {"x": x, "y": y, "button": "left"})
                    await _emit_action_event("type", {"text": text})
                    ok = await self._auto.focus_and_write_text(
                        x=x,
                        y=y,
                        text=text,
                        clear=clear,
                        submit=submit,
                    )
                    result["success"] = ok
                    result["message"] = f"Campo enfocado y texto escrito en ({x}, {y})"

                case "press" | "key":
                    key = str(action.params.get("key", "enter"))
                    await _emit_action_event("press", {"key": key})
                    ok = await self._auto.press_key(key)
                    result["success"] = ok
                    result["message"] = f"Tecla presionada: {key}" if ok else f"Error presionando tecla: {key}"

                case "hotkey":
                    keys = self._normalize_hotkey_keys(action.params.get("keys", ""))
                    keys_str = "+".join(keys)
                    await _emit_action_event("hotkey", {"keys": keys})
                    ok = await self._auto.hotkey(*keys)
                    result["success"] = ok
                    result["message"] = f"Hotkey: {keys_str}"

                case "scroll":
                    clicks = int(action.params.get("clicks", -3))
                    x = action.params.get("x")
                    y = action.params.get("y")
                    await _emit_action_event("scroll", {"clicks": clicks})
                    ok = await self._auto.scroll(clicks, x=x, y=y)
                    result["success"] = ok

                case "screenshot":
                    await _emit_action_event("screenshot", {})
                    await asyncio.sleep(0.3)
                    target_mon = int(action.params.get("monitor", 0) or config.get("vision", "target_monitor", default=0))
                    # Usar modo hybrid para incluir también texto OCR — ayuda al modelo a entender qué está en pantalla
                    _use_hybrid = config.get("vision", "screenshot_hybrid_mode", default=True)
                    _mode = "hybrid" if _use_hybrid else "computer_use"
                    screen_data = await self._vision.analyze_screen(mode=_mode, monitor=target_mon)
                    # Guardar dimensiones para escalar coordenadas futuras
                    if screen_data.get("screen_dimensions"):
                        self._screen_dims = screen_data["screen_dimensions"]
                        dims = self._screen_dims
                        logger.debug(
                            f"Dimensiones guardadas: physical={dims.get('physical_w')}x{dims.get('physical_h')} | "
                            f"logical={dims.get('logical_w')}x{dims.get('logical_h')} | "
                            f"enviado={dims.get('sent_w')}x{dims.get('sent_h')} | "
                            f"DPI={dims.get('dpi_scale_x', 1.0):.2f}x{dims.get('dpi_scale_y', 1.0):.2f}"
                        )
                    image_b64 = screen_data.get("image_base64")
                    result["success"] = bool(image_b64)
                    result["data"] = screen_data
                    result["message"] = "Captura tomada" if image_b64 else "Captura falló"

                case "chrome_list_profiles":
                    profiles = self._auto.discover_chrome_profiles()
                    result["success"] = True
                    result["data"] = {"profiles": profiles}
                    result["message"] = f"Perfiles detectados: {len(profiles)}"

                case "downloads_check":
                    limit = int(action.params.get("limit", 10))
                    recency_seconds = int(action.params.get("recency_seconds", 900))
                    expected_ext = action.params.get("expected_ext")
                    filename_contains = action.params.get("filename_contains")
                    downloads = self._auto.list_recent_downloads(
                        limit=limit,
                        recency_seconds=recency_seconds,
                        expected_ext=str(expected_ext) if expected_ext else None,
                        filename_contains=str(filename_contains) if filename_contains else None,
                    )
                    result["success"] = len(downloads) > 0
                    result["data"] = {"downloads": downloads}
                    result["message"] = f"Archivos recientes detectados: {len(downloads)}"

                case "skills_catalog":
                    skill_id = str(action.params.get("skill_id", "")).strip()
                    if skill_id:
                        try:
                            data = {
                                "enabled": bool(config.get("skills", "enabled", default=True)),
                                "skill": self._skills.get_skill(skill_id),
                            }
                        except KeyError:
                            result["message"] = f"Skill no encontrada: {skill_id}"
                            return result
                        result["success"] = True
                        result["data"] = data
                        result["message"] = f"Skill encontrada: {data['skill']['name']} ({data['skill']['id']})"
                        return result

                    data = self._skills.list_catalog()
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"Skills descubiertas: {len(data.get('skills', []))} "
                        f"en {len(data.get('roots', []))} roots"
                    )

                case "skill_install_local":
                    source_path = str(action.params.get("path", "")).strip()
                    if not source_path:
                        result["message"] = "Falta path en skill_install_local"
                        return result
                    data = self._skills.install_from_path(
                        source_path,
                        overwrite=_coerce_bool(action.params.get("overwrite", False)),
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Skill instalada localmente: {data['name']} ({data['id']})"

                case "skill_install_git":
                    repo_url = str(action.params.get("repo_url", "")).strip()
                    if not repo_url:
                        result["message"] = "Falta repo_url en skill_install_git"
                        return result
                    data = self._skills.install_from_git(
                        repo_url=repo_url,
                        ref=str(action.params.get("ref", "")).strip() or None,
                        subdir=str(action.params.get("subdir", "")).strip() or None,
                        overwrite=_coerce_bool(action.params.get("overwrite", False)),
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Skill instalada desde git: {data['name']} ({data['id']})"

                case "skill_enable":
                    skill_id = str(action.params.get("skill_id", "")).strip()
                    if not skill_id:
                        result["message"] = "Falta skill_id en skill_enable"
                        return result
                    data = self._skills.set_enabled(skill_id, True)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Skill habilitada: {data['name']} ({data['id']})"

                case "skill_disable":
                    skill_id = str(action.params.get("skill_id", "")).strip()
                    if not skill_id:
                        result["message"] = "Falta skill_id en skill_disable"
                        return result
                    data = self._skills.set_enabled(skill_id, False)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Skill deshabilitada: {data['name']} ({data['id']})"

                case "skill_uninstall":
                    skill_id = str(action.params.get("skill_id", "")).strip()
                    if not skill_id:
                        result["message"] = "Falta skill_id en skill_uninstall"
                        return result
                    data = self._skills.uninstall(skill_id)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Skill desinstalada: {data['name']} ({data['id']})"

                case "skill_run":
                    skill_id = str(action.params.get("skill_id", "")).strip()
                    tool = str(action.params.get("tool", "")).strip()
                    if not skill_id:
                        result["message"] = "Falta skill_id en skill_run"
                        return result
                    if not tool:
                        result["message"] = "Falta tool en skill_run"
                        return result

                    raw_input = action.params.get("input", {})
                    if raw_input is None:
                        input_payload = {}
                    elif isinstance(raw_input, dict):
                        input_payload = raw_input
                    else:
                        result["message"] = "El parametro input de skill_run debe ser un objeto JSON"
                        return result

                    data = await asyncio.to_thread(
                        self._skill_runtime.run_tool,
                        skill_id,
                        tool,
                        input_payload,
                        action.params.get("timeout_seconds"),
                    )
                    result["success"] = bool(data.get("success"))
                    result["data"] = data
                    if data.get("success"):
                        result["message"] = (
                            f"Skill ejecutada: {data['skill_id']}::{data['tool']} "
                            f"(exit={data.get('exit_code', 0)})"
                        )
                    else:
                        result["message"] = (
                            data.get("error")
                            or f"Fallo ejecutando {data['skill_id']}::{data['tool']}"
                        )

                case "mcp_list_servers":
                    server_id = str(action.params.get("server_id", "")).strip()
                    if server_id:
                        try:
                            data = {
                                "enabled": bool(config.get("mcp", "enabled", default=True)),
                                "server": self._mcp.get_server(server_id),
                            }
                        except KeyError:
                            result["message"] = f"Servidor MCP no encontrado: {server_id}"
                            return result
                        result["success"] = True
                        result["data"] = data
                        result["message"] = (
                            f"MCP: {data['server']['name']} "
                            f"({data['server']['transport']}, {data['server']['status']})"
                        )
                        return result

                    data = self._mcp.list_servers()
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"Servidores MCP configurados: {len(data.get('servers', []))}"
                    )

                case "payments_list_accounts":
                    account_id = str(action.params.get("account_id", "")).strip()
                    if account_id:
                        try:
                            data = {
                                "enabled": bool(config.get("payments", "enabled", default=True)),
                                "default_account_id": str(config.get("payments", "default_account_id", default="") or ""),
                                "account": self._payments.get_account(account_id),
                            }
                        except KeyError:
                            result["message"] = f"Cuenta de pago no encontrada: {account_id}"
                            return result
                        result["success"] = True
                        result["data"] = data
                        result["message"] = f"Cuenta de pago cargada: {data['account'].get('name', account_id)}"
                        return result

                    data = self._payments.list_accounts()
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Cuentas de pago detectadas: {len(data.get('accounts', []))}"

                case "gateway_status":
                    gateway = get_gateway()
                    await gateway.initialize()
                    data = await gateway.get_status()
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"Gateway {'activo' if data.get('enabled') else 'desactivado'} | "
                        f"sessions: {int(data.get('connected_sessions', 0))} | "
                        f"queue: {int(data.get('queued_notifications', 0))}"
                    )

                case "gateway_list_sessions":
                    gateway = get_gateway()
                    await gateway.initialize()
                    data = {
                        "sessions": await gateway.list_sessions(
                            channel=str(action.params.get("channel", "")).strip() or None,
                            connected_only=_coerce_bool(action.params.get("connected_only", False)),
                            limit=int(action.params.get("limit", 100)),
                        )
                    }
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Sesiones gateway: {len(data['sessions'])}"

                case "gateway_list_outbox":
                    gateway = get_gateway()
                    await gateway.initialize()
                    data = {
                        "notifications": await gateway.list_outbox(
                            channel=str(action.params.get("channel", "")).strip() or None,
                            status=str(action.params.get("status", "")).strip() or None,
                            limit=int(action.params.get("limit", 100)),
                        )
                    }
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Outbox gateway: {len(data['notifications'])} item(s)"

                case "gateway_notify":
                    gateway = get_gateway()
                    await gateway.initialize()
                    title = str(action.params.get("title", "")).strip()
                    if not title:
                        result["message"] = "Falta title en gateway_notify"
                        return result
                    payload = action.params.get("payload", {})
                    if payload is None:
                        payload = {}
                    if not isinstance(payload, dict):
                        result["message"] = "payload debe ser un objeto JSON en gateway_notify"
                        return result
                    data = await gateway.notify(
                        title=title,
                        body=str(action.params.get("body", "") or ""),
                        target=str(action.params.get("target", "")).strip() or None,
                        level=str(action.params.get("level", "info") or "info"),
                        payload=payload,
                        source_type=str(action.params.get("source_type", "") or ""),
                        source_id=str(action.params.get("source_id", "") or ""),
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"Notificacion gateway creada: {data.get('notification_id', '')} | "
                        f"estado: {data.get('status', 'queued')}"
                    )

                case "payments_get_account":
                    account_id = str(action.params.get("account_id", "")).strip()
                    if not account_id:
                        result["message"] = "Falta account_id en payments_get_account"
                        return result
                    try:
                        data = self._payments.get_account(account_id)
                    except KeyError:
                        result["message"] = f"Cuenta de pago no encontrada: {account_id}"
                        return result
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Cuenta de pago cargada: {data.get('name', account_id)}"

                case "mcp_list_tools":
                    server_id = str(action.params.get("server_id", "")).strip()
                    if not server_id:
                        result["message"] = "Falta server_id en mcp_list_tools"
                        return result
                    data = await asyncio.to_thread(
                        self._mcp_runtime.list_tools,
                        server_id,
                        str(action.params.get("cursor", "")).strip() or None,
                        action.params.get("timeout_seconds"),
                    )
                    result["success"] = bool(data.get("success"))
                    result["data"] = data
                    result["message"] = (
                        f"Tools MCP descubiertas en {server_id}: {len(data.get('tools', []))}"
                        if data.get("success")
                        else (data.get("error") or f"No se pudieron listar tools de {server_id}")
                    )

                case "mcp_call_tool":
                    server_id = str(action.params.get("server_id", "")).strip()
                    tool = str(action.params.get("tool", action.params.get("name", ""))).strip()
                    logger.info(
                        f"mcp_call_tool: server_id={server_id!r}, tool={tool!r}, "
                        f"raw_params={action.params}"
                    )
                    if not server_id:
                        result["message"] = "Falta server_id en mcp_call_tool"
                        return result
                    if not tool:
                        result["message"] = "Falta tool en mcp_call_tool"
                        return result
                    raw_arguments = action.params.get("arguments", action.params.get("input", {}))
                    if raw_arguments is None:
                        arguments_payload = {}
                    elif isinstance(raw_arguments, dict):
                        arguments_payload = raw_arguments
                    else:
                        logger.warning(
                            f"mcp_call_tool: arguments no es dict: type={type(raw_arguments).__name__}, "
                            f"value={raw_arguments!r}"
                        )
                        result["message"] = "El parametro arguments de mcp_call_tool debe ser un objeto JSON"
                        return result
                    data = await asyncio.to_thread(
                        self._mcp_runtime.call_tool,
                        server_id,
                        tool,
                        arguments_payload,
                        action.params.get("timeout_seconds"),
                    )
                    result["success"] = bool(data.get("success"))
                    result["data"] = data
                    result["message"] = (
                        f"Tool MCP ejecutada: {server_id}::{tool}"
                        if data.get("success")
                        else (data.get("error") or f"Fallo llamando {server_id}::{tool}")
                    )

                case "schedule_list_jobs":
                    scheduler = get_scheduler()
                    await scheduler.initialize()
                    job_id = str(action.params.get("job_id", "")).strip()
                    if job_id:
                        try:
                            data = {"job": await scheduler.get_job(job_id)}
                        except KeyError:
                            result["message"] = f"Job programado no encontrado: {job_id}"
                            return result
                        result["success"] = True
                        result["data"] = data
                        result["message"] = f"Job programado: {data['job']['name']} ({data['job']['job_id']})"
                        return result

                    data = {"jobs": await scheduler.list_jobs()}
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Jobs programados: {len(data['jobs'])}"

                case "schedule_create_job":
                    scheduler = get_scheduler()
                    await scheduler.initialize()
                    raw_payload = action.params.get("payload", {})
                    if not isinstance(raw_payload, dict):
                        result["message"] = "payload debe ser un objeto JSON en schedule_create_job"
                        return result
                    data = await scheduler.create_job(
                        name=str(action.params.get("name", "")).strip() or "Job programado",
                        task_type=str(action.params.get("task_type", "")).strip(),
                        payload=raw_payload,
                        trigger_type=str(action.params.get("trigger_type", "")).strip(),
                        interval_seconds=action.params.get("interval_seconds"),
                        cron_expression=str(action.params.get("cron_expression", "")).strip() or None,
                        event_name=str(action.params.get("event_name", "")).strip() or None,
                        webhook_path=str(action.params.get("webhook_path", "")).strip() or None,
                        webhook_secret=str(action.params.get("webhook_secret", "")).strip() or None,
                        heartbeat_key=str(action.params.get("heartbeat_key", "")).strip() or None,
                        heartbeat_interval_seconds=action.params.get("heartbeat_interval_seconds"),
                        max_retries=int(action.params.get("max_retries", 0)),
                        retry_backoff_seconds=int(action.params.get("retry_backoff_seconds", 30)),
                        retry_backoff_multiplier=float(action.params.get("retry_backoff_multiplier", 2.0)),
                        enabled=_coerce_bool(action.params.get("enabled", True), default=True),
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Job programado creado: {data['name']} ({data['job_id']})"

                case "schedule_update_job":
                    scheduler = get_scheduler()
                    await scheduler.initialize()
                    job_id = str(action.params.get("job_id", "")).strip()
                    if not job_id:
                        result["message"] = "Falta job_id en schedule_update_job"
                        return result
                    raw_payload = action.params.get("payload")
                    if raw_payload is not None and not isinstance(raw_payload, dict):
                        result["message"] = "payload debe ser un objeto JSON en schedule_update_job"
                        return result
                    cron_expression_value = None
                    if "cron_expression" in action.params:
                        cron_expression_value = (
                            str(action.params.get("cron_expression", "")).strip() or None
                        )
                    event_name_value = None
                    if "event_name" in action.params:
                        event_name_value = str(action.params.get("event_name", "")).strip() or ""
                    webhook_path_value = None
                    if "webhook_path" in action.params:
                        webhook_path_value = str(action.params.get("webhook_path", "")).strip() or ""
                    webhook_secret_value = None
                    if "webhook_secret" in action.params:
                        webhook_secret_value = str(action.params.get("webhook_secret", "")).strip()
                    heartbeat_key_value = None
                    if "heartbeat_key" in action.params:
                        heartbeat_key_value = str(action.params.get("heartbeat_key", "")).strip() or ""
                    data = await scheduler.update_job(
                        job_id,
                        name=str(action.params.get("name", "")).strip() or None,
                        payload=raw_payload,
                        interval_seconds=action.params.get("interval_seconds"),
                        cron_expression=cron_expression_value,
                        event_name=event_name_value,
                        webhook_path=webhook_path_value,
                        webhook_secret=webhook_secret_value,
                        heartbeat_key=heartbeat_key_value,
                        heartbeat_interval_seconds=(
                            action.params.get("heartbeat_interval_seconds")
                            if "heartbeat_interval_seconds" in action.params
                            else None
                        ),
                        max_retries=int(action.params.get("max_retries")) if "max_retries" in action.params else None,
                        retry_backoff_seconds=(
                            int(action.params.get("retry_backoff_seconds"))
                            if "retry_backoff_seconds" in action.params
                            else None
                        ),
                        retry_backoff_multiplier=(
                            float(action.params.get("retry_backoff_multiplier"))
                            if "retry_backoff_multiplier" in action.params
                            else None
                        ),
                        enabled=_coerce_bool(action.params.get("enabled"), default=False) if "enabled" in action.params else None,
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Job programado actualizado: {data['name']} ({data['job_id']})"

                case "schedule_delete_job":
                    scheduler = get_scheduler()
                    await scheduler.initialize()
                    job_id = str(action.params.get("job_id", "")).strip()
                    if not job_id:
                        result["message"] = "Falta job_id en schedule_delete_job"
                        return result
                    data = await scheduler.delete_job(job_id)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Job programado eliminado: {data['job']['name']} ({data['job']['job_id']})"

                case "schedule_run_job":
                    scheduler = get_scheduler()
                    await scheduler.initialize()
                    job_id = str(action.params.get("job_id", "")).strip()
                    if not job_id:
                        result["message"] = "Falta job_id en schedule_run_job"
                        return result
                    data = await scheduler.run_job_now(job_id)
                    result["success"] = data.get("status") == "success"
                    result["data"] = data
                    if result["success"]:
                        result["message"] = f"Job ejecutado manualmente: {job_id}"
                    else:
                        retry_details = data.get("result", {}) if isinstance(data.get("result"), dict) else {}
                        if retry_details.get("retry_scheduled"):
                            result["message"] = (
                                f"Fallo ejecutando job {job_id}, pero se programo retry #{retry_details.get('retry_attempt', 0)} "
                                f"en {retry_details.get('retry_delay_seconds', 0)}s"
                            )
                        else:
                            result["message"] = data.get("error") or f"Fallo ejecutando job {job_id}"

                case "schedule_list_runs":
                    scheduler = get_scheduler()
                    await scheduler.initialize()
                    data = {
                        "runs": await scheduler.list_runs(
                            job_id=str(action.params.get("job_id", "")).strip() or None,
                            limit=int(action.params.get("limit", 20)),
                        )
                    }
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Runs registrados: {len(data['runs'])}"

                case "schedule_list_checkpoints":
                    scheduler = get_scheduler()
                    await scheduler.initialize()
                    data = {
                        "checkpoints": await scheduler.list_checkpoints(
                            job_id=str(action.params.get("job_id", "")).strip() or None,
                            run_id=str(action.params.get("run_id", "")).strip() or None,
                            limit=int(action.params.get("limit", 50)),
                        ),
                        "recovery": scheduler.get_recovery_summary(),
                    }
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Checkpoints registrados: {len(data['checkpoints'])}"

                case "schedule_recovery_status":
                    scheduler = get_scheduler()
                    await scheduler.initialize()
                    data = {"recovery": scheduler.get_recovery_summary()}
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        "Estado de recovery del scheduler: "
                        f"{data['recovery'].get('interrupted_runs', 0)} runs recuperados"
                    )

                case "budget_summary":
                    tracker = get_cost_tracker()
                    await tracker.initialize()
                    session_id = str(action.params.get("session_id", "")).strip() or None
                    current_mode = str(action.params.get("current_mode", "")).strip()
                    worker_id = str(action.params.get("worker_id", "")).strip() or None
                    worker_kind = str(action.params.get("worker_kind", "")).strip()
                    data = await tracker.get_summary(
                        session_id=session_id,
                        current_mode=current_mode,
                        worker_id=worker_id,
                        worker_kind=worker_kind,
                    )
                    result["success"] = True
                    result["data"] = data
                    session_total = float((data.get("current_session") or {}).get("total_cost_usd", 0.0) or 0.0)
                    today_total = float((data.get("today") or {}).get("total_cost_usd", 0.0) or 0.0)
                    month_total = float((data.get("month") or {}).get("total_cost_usd", 0.0) or 0.0)
                    result["message"] = (
                        f"Costos listos | sesion: ${session_total:.4f} | "
                        f"hoy: ${today_total:.4f} | mes: ${month_total:.4f}"
                    )

                case "budget_weekly_report":
                    tracker = get_cost_tracker()
                    await tracker.initialize()
                    session_id = str(action.params.get("session_id", "")).strip() or None
                    current_mode = str(action.params.get("current_mode", "")).strip()
                    week_offset = int(action.params.get("week_offset", 0))
                    include_current_week = _coerce_bool(
                        action.params.get("include_current_week", False),
                        default=False,
                    )
                    top_n = int(action.params.get("top_n", 5))
                    data = await tracker.get_weekly_report(
                        session_id=session_id,
                        current_mode=current_mode,
                        week_offset=week_offset,
                        include_current_week=include_current_week,
                        top_n=top_n,
                    )
                    result["success"] = True
                    result["data"] = data
                    weekly_total = float((data.get("totals") or {}).get("total_cost_usd", 0.0) or 0.0)
                    delta_total = float(data.get("delta_total_cost_usd", 0.0) or 0.0)
                    result["message"] = (
                        f"Reporte semanal listo | {data.get('window_label', 'semana')} | "
                        f"total: ${weekly_total:.4f} | delta: ${delta_total:.4f}"
                    )

                case "budget_list_events":
                    tracker = get_cost_tracker()
                    await tracker.initialize()
                    session_id = str(action.params.get("session_id", "")).strip() or None
                    limit = int(action.params.get("limit", 30))
                    events = await tracker.list_events(session_id=session_id, limit=limit)
                    data = {
                        "session_id": session_id,
                        "events": events,
                    }
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Eventos de costo registrados: {len(events)}"

                case "schedule_emit_event":
                    scheduler = get_scheduler()
                    await scheduler.initialize()
                    event_name = str(action.params.get("event_name", "")).strip()
                    if not event_name:
                        result["message"] = "Falta event_name en schedule_emit_event"
                        return result
                    payload = action.params.get("payload", {})
                    if not isinstance(payload, dict):
                        result["message"] = "payload debe ser un objeto JSON en schedule_emit_event"
                        return result
                    data = await scheduler.emit_event(event_name, payload=payload)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"Evento emitido: {event_name} | jobs ejecutados: {data.get('executed_jobs', 0)}"
                    )

                case "schedule_emit_heartbeat":
                    scheduler = get_scheduler()
                    await scheduler.initialize()
                    heartbeat_key = (
                        str(action.params.get("heartbeat_key", "")).strip() or "system"
                    )
                    payload = action.params.get("payload", {})
                    if not isinstance(payload, dict):
                        result["message"] = "payload debe ser un objeto JSON en schedule_emit_heartbeat"
                        return result
                    data = await scheduler.emit_heartbeat(heartbeat_key, payload=payload)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"Heartbeat emitido: {heartbeat_key} | jobs ejecutados: {data.get('executed_jobs', 0)}"
                    )

                case "schedule_trigger_webhook":
                    scheduler = get_scheduler()
                    await scheduler.initialize()
                    webhook_path = str(action.params.get("webhook_path", "")).strip()
                    if not webhook_path:
                        result["message"] = "Falta webhook_path en schedule_trigger_webhook"
                        return result
                    payload = action.params.get("payload", {})
                    if not isinstance(payload, dict):
                        result["message"] = "payload debe ser un objeto JSON en schedule_trigger_webhook"
                        return result
                    data = await scheduler.trigger_webhook(
                        webhook_path,
                        payload=payload,
                        secret=str(action.params.get("secret", "")).strip() or None,
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"Webhook disparado: {webhook_path} | jobs ejecutados: {data.get('executed_jobs', 0)}"
                    )

                case "file_write_text":
                    path = str(action.params.get("path", "")).strip()
                    text = str(action.params.get("text", ""))
                    append = _coerce_bool(action.params.get("append", False))
                    if not path:
                        result["message"] = "Falta path en file_write_text"
                        return result
                    if self._workspace:
                        data = self._workspace.write_text_file(path=path, text=text, append=append)
                    else:
                        data = self._auto.write_text_file(path=path, text=text, append=append)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Archivo escrito en {data['path']}"

                case "file_exists":
                    path = str(action.params.get("path", "")).strip()
                    if not path:
                        result["message"] = "Falta path en file_exists"
                        return result
                    if self._workspace:
                        data = self._workspace.file_exists(path)
                    else:
                        data = self._auto.file_exists(path)
                    result["success"] = bool(data.get("exists"))
                    result["data"] = data
                    result["message"] = (
                        f"Archivo confirmado en disco: {data['path']}"
                        if data.get("exists")
                        else f"No existe el archivo esperado: {data['path']}"
                    )

                case "file_list":
                    if not self._workspace:
                        result["message"] = "Workspace manager no disponible"
                        return result
                    data = self._workspace.list_files(
                        path=str(action.params.get("path", "")).strip() or None,
                        pattern=str(action.params.get("pattern", "*") or "*"),
                        recursive=_coerce_bool(action.params.get("recursive", False)),
                        include_hidden=_coerce_bool(action.params.get("include_hidden", False)),
                        include_dirs=_coerce_bool(action.params.get("include_dirs", False)),
                        max_results=int(action.params.get("max_results", 200)),
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"Entradas listadas: {data['count']} en {data['base_path']}"
                    )

                case "file_read_text":
                    if not self._workspace:
                        result["message"] = "Workspace manager no disponible"
                        return result
                    path = str(action.params.get("path", "")).strip()
                    if not path:
                        result["message"] = "Falta path en file_read_text"
                        return result
                    data = self._workspace.read_text_file(
                        path=path,
                        start_line=int(action.params.get("start_line", 1)),
                        max_lines=int(action.params.get("max_lines", 200)),
                        max_chars=int(action.params.get("max_chars", 20000)),
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"Archivo leido: {data['relative_path']} lineas {data['start_line']}-{data['end_line']}"
                    )

                case "file_read_batch":
                    if not self._workspace:
                        result["message"] = "Workspace manager no disponible"
                        return result
                    raw_paths = action.params.get("paths", [])
                    if not isinstance(raw_paths, list) or not raw_paths:
                        result["message"] = "Falta paths en file_read_batch"
                        return result
                    data = self._workspace.read_text_files(
                        [str(path) for path in raw_paths],
                        start_line=int(action.params.get("start_line", 1)),
                        max_lines=int(action.params.get("max_lines", 200)),
                        max_chars_per_file=int(action.params.get("max_chars_per_file", 20000)),
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Archivos leidos por lote: {data['count']}"

                case "file_search_text":
                    if not self._workspace:
                        result["message"] = "Workspace manager no disponible"
                        return result
                    query = str(action.params.get("query", "")).strip()
                    if not query:
                        result["message"] = "Falta query en file_search_text"
                        return result
                    data = self._workspace.search_text(
                        query=query,
                        path=str(action.params.get("path", "")).strip() or None,
                        pattern=str(action.params.get("pattern", "*") or "*"),
                        recursive=_coerce_bool(action.params.get("recursive", True), default=True),
                        case_sensitive=_coerce_bool(action.params.get("case_sensitive", False)),
                        max_results=int(action.params.get("max_results", 50)),
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"Coincidencias encontradas: {data['count']} para '{data['query']}'"
                    )

                case "file_replace_text":
                    if not self._workspace:
                        result["message"] = "Workspace manager no disponible"
                        return result
                    path = str(action.params.get("path", "")).strip()
                    find_text = str(action.params.get("find", ""))
                    replace_text = str(action.params.get("replace", ""))
                    if not path:
                        result["message"] = "Falta path en file_replace_text"
                        return result
                    if not find_text:
                        result["message"] = "Falta find en file_replace_text"
                        return result
                    data = self._workspace.replace_text(
                        path=path,
                        find=find_text,
                        replace=replace_text,
                        count=int(action.params.get("count", 1)),
                    )
                    result["success"] = bool(data.get("changed"))
                    result["data"] = data
                    result["message"] = (
                        f"Reemplazos aplicados: {data['replaced_count']} en {data['path']}"
                        if data.get("changed")
                        else f"No se encontro el texto a reemplazar en {data['path']}"
                    )

                case "workspace_snapshot":
                    if not self._workspace:
                        result["message"] = "Workspace manager no disponible"
                        return result
                    data = self._workspace.workspace_snapshot(
                        path=str(action.params.get("path", "")).strip() or None,
                        max_entries=int(action.params.get("max_entries", 80)),
                        include_git=_coerce_bool(action.params.get("include_git", True), default=True),
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"Workspace detectado: {data['relative_project_root']} | "
                        f"tipos: {', '.join(data['detected_kinds']) or 'desconocido'}"
                    )

                case "git_status":
                    if not self._workspace:
                        result["message"] = "Workspace manager no disponible"
                        return result
                    data = self._workspace.git_status(
                        path=str(action.params.get("path", "")).strip() or None,
                        max_entries=int(action.params.get("max_entries", 100)),
                    )
                    result["success"] = bool(data.get("available"))
                    result["data"] = data
                    if data.get("is_repo"):
                        result["message"] = (
                            f"Git status en {data['relative_repo_root']}: {data['count']} cambios"
                        )
                    else:
                        result["message"] = data.get("reason", "git status no disponible")

                case "git_changed_files":
                    if not self._workspace:
                        result["message"] = "Workspace manager no disponible"
                        return result
                    data = self._workspace.git_changed_files(
                        path=str(action.params.get("path", "")).strip() or None,
                        staged=_coerce_bool(action.params.get("staged", False)),
                        max_entries=int(action.params.get("max_entries", 100)),
                    )
                    result["success"] = bool(data.get("available"))
                    result["data"] = data
                    if data.get("is_repo"):
                        result["message"] = f"Archivos cambiados detectados: {data['count']}"
                    else:
                        result["message"] = data.get("reason", "git changed files no disponible")

                case "git_diff":
                    if not self._workspace:
                        result["message"] = "Workspace manager no disponible"
                        return result
                    data = self._workspace.git_diff(
                        path=str(action.params.get("path", "")).strip() or None,
                        staged=_coerce_bool(action.params.get("staged", False)),
                        ref=str(action.params.get("ref", "")).strip() or None,
                        max_chars=int(action.params.get("max_chars", 20000)),
                    )
                    result["success"] = bool(data.get("available"))
                    result["data"] = data
                    if data.get("is_repo"):
                        content = str(data.get("content", ""))
                        result["message"] = (
                            f"Git diff obtenido ({len(content)} chars)"
                            + (" truncado" if data.get("truncated") else "")
                        )
                    else:
                        result["message"] = data.get("reason", "git diff no disponible")

                case "git_log":
                    if not self._workspace:
                        result["message"] = "Workspace manager no disponible"
                        return result
                    data = self._workspace.git_log(
                        path=str(action.params.get("path", "")).strip() or None,
                        limit=int(action.params.get("limit", 10)),
                    )
                    result["success"] = bool(data.get("available"))
                    result["data"] = data
                    if data.get("is_repo"):
                        result["message"] = f"Git log obtenido: {data['count']} commits"
                    else:
                        result["message"] = data.get("reason", "git log no disponible")

                case "code_outline":
                    if not self._workspace:
                        result["message"] = "Workspace manager no disponible"
                        return result
                    path = str(action.params.get("path", "")).strip()
                    if not path:
                        result["message"] = "Falta path en code_outline"
                        return result
                    data = self._workspace.code_outline(
                        path=path,
                        max_symbols=int(action.params.get("max_symbols", 200)),
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"Outline extraido: {data['count']} simbolos en {data['relative_path']}"
                    )

                case "code_related_files":
                    if not self._workspace:
                        result["message"] = "Workspace manager no disponible"
                        return result
                    path = str(action.params.get("path", "")).strip()
                    if not path:
                        result["message"] = "Falta path en code_related_files"
                        return result
                    data = self._workspace.code_related_files(
                        path=path,
                        max_results=int(action.params.get("max_results", 20)),
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"Archivos relacionados encontrados: {data['count']} para {data['relative_path']}"
                    )

                case "ide_detect":
                    if not self._ide:
                        result["message"] = "IDE manager no disponible"
                        return result
                    data = self._ide.detect_editors()
                    data["bridge_connected"] = bool(self._editor_bridge and self._editor_bridge.is_connected)
                    if self._editor_bridge:
                        data["bridge_editor"] = self._editor_bridge.editor_info
                    result["success"] = True
                    result["data"] = data
                    bridge_suffix = " | bridge activo" if data["bridge_connected"] else ""
                    result["message"] = f"Editores detectados: {data['count']}{bridge_suffix}"

                case "ide_state":
                    detected = self._ide.detect_editors() if self._ide else {"preferred": "", "available": [], "count": 0}
                    data: dict[str, Any] = {
                        "bridge_connected": bool(self._editor_bridge and self._editor_bridge.is_connected),
                        "preferred_editor": detected.get("preferred", ""),
                        "detected_editors": detected.get("available", []),
                        "detected_count": detected.get("count", 0),
                    }
                    if self._editor_bridge and self._editor_bridge.is_connected:
                        data["editor_info"] = self._editor_bridge.editor_info
                        try:
                            bridge_response = await self._editor_bridge.get_state()
                            data["state"] = bridge_response.get("state", self._editor_bridge.current_state)
                            cache_suffix = ""
                        except Exception:
                            data["state"] = self._editor_bridge.current_state
                            cache_suffix = " | usando cache"
                        active_file = data["state"].get("activeFile") or {}
                        active_path = str(active_file.get("path", "")).strip()
                        result["message"] = (
                            f"IDE bridge conectado"
                            + (f" | archivo activo: {active_path}" if active_path else " | sin archivo activo")
                            + cache_suffix
                        )
                    else:
                        data["editor_info"] = self._editor_bridge.editor_info if self._editor_bridge else {}
                        data["state"] = self._editor_bridge.current_state if self._editor_bridge else {}
                        result["message"] = f"IDE bridge no conectado | editores detectados: {data['detected_count']}"
                    result["success"] = True
                    result["data"] = data

                case "ide_active_file":
                    if not self._editor_bridge or not self._editor_bridge.is_connected:
                        result["message"] = "IDE bridge no conectado"
                        return result
                    try:
                        data = await self._editor_bridge.get_active_file()
                        active_file = data.get("activeFile")
                        cache_suffix = ""
                    except Exception:
                        active_file = self._editor_bridge.current_state.get("activeFile")
                        cache_suffix = " | usando cache"
                    if not isinstance(active_file, dict):
                        result["message"] = "No hay archivo activo en el IDE"
                        return result
                    result["success"] = True
                    result["data"] = active_file
                    result["message"] = f"Archivo activo del IDE: {active_file.get('path', '')}{cache_suffix}"

                case "ide_selection":
                    if not self._editor_bridge or not self._editor_bridge.is_connected:
                        result["message"] = "IDE bridge no conectado"
                        return result
                    try:
                        data = await self._editor_bridge.get_selection()
                        selection = data.get("selection")
                        cache_suffix = ""
                    except Exception:
                        selection = self._editor_bridge.current_state.get("selection")
                        cache_suffix = " | usando cache"
                    if not isinstance(selection, dict):
                        result["message"] = "No hay seleccion activa en el IDE"
                        return result
                    selected_text = str(selection.get("text", ""))
                    result["success"] = True
                    result["data"] = selection
                    result["message"] = f"Seleccion del IDE obtenida ({len(selected_text)} chars){cache_suffix}"

                case "ide_workspace_folders":
                    if not self._editor_bridge or not self._editor_bridge.is_connected:
                        result["message"] = "IDE bridge no conectado"
                        return result
                    try:
                        data = await self._editor_bridge.get_workspace_folders()
                        workspace_folders = data.get("workspaceFolders", [])
                        cache_suffix = ""
                    except Exception:
                        workspace_folders = self._editor_bridge.current_state.get("workspaceFolders", [])
                        cache_suffix = " | usando cache"
                    result["success"] = True
                    result["data"] = {
                        "workspaceFolders": workspace_folders,
                        "count": len(workspace_folders),
                    }
                    result["message"] = f"Workspaces activos del IDE: {len(workspace_folders)}{cache_suffix}"

                case "ide_diagnostics":
                    if not self._editor_bridge or not self._editor_bridge.is_connected:
                        result["message"] = "IDE bridge no conectado"
                        return result
                    path = str(action.params.get("path", "")).strip() or None
                    try:
                        data = await self._editor_bridge.get_diagnostics(path=path)
                        diagnostics = data.get("diagnostics")
                        cache_suffix = ""
                    except Exception:
                        if path:
                            raise
                        diagnostics = self._editor_bridge.current_state.get("diagnostics")
                        cache_suffix = " | usando cache"
                    if not isinstance(diagnostics, dict):
                        result["message"] = "No se pudieron obtener diagnosticos del IDE"
                        return result
                    result["success"] = True
                    result["data"] = diagnostics
                    target_path = str(diagnostics.get("path") or path or "")
                    result["message"] = (
                        f"Diagnosticos del IDE: {int(diagnostics.get('count', 0))}"
                        + (f" para {target_path}" if target_path else "")
                        + cache_suffix
                    )

                case "ide_symbols":
                    if not self._editor_bridge or not self._editor_bridge.is_connected:
                        result["message"] = "IDE bridge no conectado"
                        return result
                    path = str(action.params.get("path", "")).strip() or None
                    data = await self._editor_bridge.get_document_symbols(path=path)
                    symbols = data.get("symbols")
                    if not isinstance(symbols, dict):
                        result["message"] = "No se pudieron obtener simbolos del IDE"
                        return result
                    result["success"] = True
                    result["data"] = symbols
                    target_path = str(symbols.get("path") or path or "")
                    result["message"] = (
                        f"Simbolos del IDE: {int(symbols.get('count', 0))}"
                        + (f" para {target_path}" if target_path else "")
                    )

                case "ide_find_symbol":
                    if not self._editor_bridge or not self._editor_bridge.is_connected:
                        result["message"] = "IDE bridge no conectado"
                        return result
                    query = str(action.params.get("query", "")).strip()
                    if not query:
                        result["message"] = "Falta query en ide_find_symbol"
                        return result
                    path = str(action.params.get("path", "")).strip() or None
                    kind = str(action.params.get("kind", "")).strip() or None
                    max_results = int(action.params.get("max_results", 10))
                    data = await self._editor_bridge.find_symbols(
                        query=query,
                        path=path,
                        kind=kind,
                        max_results=max_results,
                    )
                    symbols = data.get("symbols")
                    if not isinstance(symbols, dict):
                        result["message"] = "No se pudieron buscar simbolos en el IDE"
                        return result
                    result["success"] = True
                    result["data"] = symbols
                    target_path = str(symbols.get("path") or path or "")
                    kind_suffix = f", kind={kind}" if kind else ""
                    result["message"] = (
                        f"Busqueda de simbolos: {int(symbols.get('count', 0))} resultados para '{query}'{kind_suffix}"
                        + (f" en {target_path}" if target_path else "")
                    )

                case "ide_reveal_symbol":
                    if not self._editor_bridge or not self._editor_bridge.is_connected:
                        result["message"] = "IDE bridge no conectado"
                        return result
                    query = str(action.params.get("query", "")).strip()
                    if not query:
                        result["message"] = "Falta query en ide_reveal_symbol"
                        return result
                    path = str(action.params.get("path", "")).strip() or None
                    kind = str(action.params.get("kind", "")).strip() or None
                    occurrence = max(1, int(action.params.get("occurrence", 1)))
                    data = await self._editor_bridge.reveal_symbol(
                        query=query,
                        path=path,
                        kind=kind,
                        occurrence=occurrence,
                        preserve_focus=_coerce_bool(action.params.get("preserve_focus", False)),
                    )
                    reveal = data.get("reveal")
                    if not isinstance(reveal, dict):
                        result["message"] = "El IDE no devolvio resultado de reveal_symbol"
                        return result
                    result["success"] = bool(reveal.get("revealed"))
                    result["data"] = reveal
                    symbol = reveal.get("symbol") or {}
                    symbol_name = str(symbol.get("name") or query)
                    target_path = str(reveal.get("path") or path or "")
                    result["message"] = (
                        f"Simbolo revelado: {symbol_name}"
                        + (f" en {target_path}" if target_path else "")
                    )

                case "ide_reveal_range":
                    if not self._editor_bridge or not self._editor_bridge.is_connected:
                        result["message"] = "IDE bridge no conectado"
                        return result
                    path = str(action.params.get("path", "")).strip()
                    if not path:
                        result["message"] = "Falta path en ide_reveal_range"
                        return result
                    data = await self._editor_bridge.reveal_range(
                        path=path,
                        start_line=int(action.params.get("start_line", 1)),
                        start_column=int(action.params.get("start_column", 1)),
                        end_line=int(action.params.get("end_line", action.params.get("start_line", 1))),
                        end_column=int(action.params.get("end_column", action.params.get("start_column", 1))),
                        preserve_focus=_coerce_bool(action.params.get("preserve_focus", False)),
                    )
                    reveal = data.get("reveal")
                    if not isinstance(reveal, dict):
                        result["message"] = "El IDE no devolvio resultado de navegacion"
                        return result
                    result["success"] = bool(reveal.get("revealed"))
                    result["data"] = reveal
                    reveal_range = reveal.get("range") or {}
                    start = reveal_range.get("start") or {}
                    end = reveal_range.get("end") or {}
                    result["message"] = (
                        f"Rango revelado en {reveal.get('path', path)} "
                        f"({start.get('line', 1)}:{start.get('column', 1)} -> "
                        f"{end.get('line', 1)}:{end.get('column', 1)})"
                    )

                case "ide_open_diagnostic" | "ide_next_diagnostic" | "ide_prev_diagnostic":
                    if not self._editor_bridge or not self._editor_bridge.is_connected:
                        result["message"] = "IDE bridge no conectado"
                        return result
                    path = str(action.params.get("path", "")).strip() or None
                    direction = None
                    if action.type == "ide_next_diagnostic":
                        direction = "next"
                    elif action.type == "ide_prev_diagnostic":
                        direction = "previous"
                    index = None
                    if direction is None and "index" in action.params:
                        index = int(action.params.get("index", 0))
                    data = await self._editor_bridge.open_diagnostic(
                        path=path,
                        index=index,
                        direction=direction,
                        preserve_focus=_coerce_bool(action.params.get("preserve_focus", False)),
                    )
                    diag = data.get("diagnostic")
                    if not isinstance(diag, dict):
                        result["message"] = "El IDE no devolvio diagnostico navegable"
                        return result
                    result["success"] = bool(diag.get("opened"))
                    result["data"] = diag
                    detail = diag.get("item") or {}
                    message = str(detail.get("message") or "diagnostico")
                    target_path = str(diag.get("path") or path or "")
                    result["message"] = (
                        f"Diagnostico abierto: {message}"
                        + (f" en {target_path}" if target_path else "")
                    )

                case "ide_apply_edit":
                    if not self._editor_bridge or not self._editor_bridge.is_connected:
                        result["message"] = "IDE bridge no conectado"
                        return result
                    path = str(action.params.get("path", "")).strip()
                    text = str(action.params.get("text", ""))
                    if not path:
                        result["message"] = "Falta path en ide_apply_edit"
                        return result
                    if "text" not in action.params:
                        result["message"] = "Falta text en ide_apply_edit"
                        return result
                    data = await self._editor_bridge.apply_edit(
                        path=path,
                        text=text,
                        start_line=int(action.params.get("start_line", 1)),
                        start_column=int(action.params.get("start_column", 1)),
                        end_line=int(action.params.get("end_line", action.params.get("start_line", 1))),
                        end_column=int(action.params.get("end_column", action.params.get("start_column", 1))),
                        save=_coerce_bool(action.params.get("save", False)),
                    )
                    edit_result = data.get("edit")
                    if not isinstance(edit_result, dict):
                        result["message"] = "El IDE no devolvio resultado de edicion"
                        return result
                    result["success"] = bool(edit_result.get("applied"))
                    result["data"] = edit_result
                    result["message"] = (
                        f"Edicion aplicada en {edit_result.get('path', path)}"
                        + (" y guardada" if edit_result.get("saved") else "")
                    )

                case "ide_apply_workspace_edits":
                    if not self._editor_bridge or not self._editor_bridge.is_connected:
                        result["message"] = "IDE bridge no conectado"
                        return result
                    edits = action.params.get("edits", [])
                    if isinstance(edits, dict):
                        edits = [edits]
                    if not isinstance(edits, list) or not edits:
                        result["message"] = "Falta edits en ide_apply_workspace_edits"
                        return result
                    data = await self._editor_bridge.apply_workspace_edits(
                        edits=edits,
                        save=_coerce_bool(action.params.get("save", False)),
                    )
                    edit_result = data.get("workspace_edit")
                    if not isinstance(edit_result, dict):
                        result["message"] = "El IDE no devolvio resultado de edicion multiple"
                        return result
                    result["success"] = bool(edit_result.get("applied"))
                    result["data"] = edit_result
                    result["message"] = (
                        f"Ediciones multiples aplicadas: {int(edit_result.get('edit_count', 0))}"
                        + (f" en {int(edit_result.get('file_count', 0))} archivos" if edit_result.get("file_count") is not None else "")
                        + (" y guardadas" if edit_result.get("saved") else "")
                    )

                case "ide_open_workspace":
                    if not self._ide:
                        result["message"] = "IDE manager no disponible"
                        return result
                    data = self._ide.open_workspace(
                        path=str(action.params.get("path", "")).strip() or None,
                        editor_key=str(action.params.get("editor_key", "")).strip() or None,
                        new_window=_coerce_bool(action.params.get("new_window", False)),
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"{data['editor_name']} abierto en {data['path']}"

                case "ide_open_file":
                    if not self._ide:
                        result["message"] = "IDE manager no disponible"
                        return result
                    path = str(action.params.get("path", "")).strip()
                    if not path:
                        result["message"] = "Falta path en ide_open_file"
                        return result
                    data = self._ide.open_file(
                        path=path,
                        line=int(action.params.get("line", 1)),
                        column=int(action.params.get("column", 1)),
                        editor_key=str(action.params.get("editor_key", "")).strip() or None,
                        new_window=_coerce_bool(action.params.get("new_window", False)),
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"{data['editor_name']} abrio {data['path']}:{data['line']}:{data['column']}"
                    )

                case "ide_open_diff":
                    if not self._ide:
                        result["message"] = "IDE manager no disponible"
                        return result
                    left_path = str(action.params.get("left_path", "")).strip()
                    right_path = str(action.params.get("right_path", "")).strip()
                    if not left_path or not right_path:
                        result["message"] = "Faltan left_path y right_path en ide_open_diff"
                        return result
                    data = self._ide.open_diff(
                        left_path=left_path,
                        right_path=right_path,
                        editor_key=str(action.params.get("editor_key", "")).strip() or None,
                        new_window=_coerce_bool(action.params.get("new_window", False)),
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"{data['editor_name']} abrio diff entre {data['left_path']} y {data['right_path']}"
                    )

                case "terminal_list":
                    if not self._terminals:
                        result["message"] = "Terminal manager no disponible"
                        return result
                    data = {
                        "shells": self._terminals.list_shells(),
                        "sessions": self._terminals.list_sessions(),
                    }
                    result["success"] = True
                    result["data"] = data
                    result["message"] = (
                        f"Shells detectadas: {len(data['shells'])} | sesiones: {len(data['sessions'])}"
                    )

                case "terminal_run":
                    if not self._terminals:
                        result["message"] = "Terminal manager no disponible"
                        return result
                    command = str(action.params.get("command", "")).strip()
                    shell_key = action.params.get("shell")
                    cwd = action.params.get("cwd")
                    task_type = str(action.params.get("task_type", "auto"))
                    if not command:
                        result["message"] = "Falta command en terminal_run"
                        return result
                    # Normalizar rutas Windows: LLMs suelen escapar backslashes
                    # (\\\\→\\, \\→\) lo cual rompe PowerShell paths
                    resolved_shell = str(shell_key) if shell_key else "powershell"
                    if resolved_shell in ("powershell", "cmd"):
                        command = command.replace("\\\\", "\\")
                    data = await self._terminals.run_command(
                        command=command,
                        cwd=str(cwd) if cwd else None,
                        shell_key=str(shell_key) if shell_key else None,
                        task_type=task_type,
                    )
                    result["success"] = data.get("return_code", 1) == 0
                    result["data"] = data
                    result["message"] = (
                        f"Terminal {data['shell_name']} rc={data.get('return_code')} | "
                        f"{data.get('output_preview', '')}"
                    )

                case "chrome_open_profile":
                    query = action.params.get("query")
                    url = action.params.get("url")
                    new_window = _coerce_bool(action.params.get("new_window", True), default=True)
                    launch = self._auto.open_chrome_profile(
                        query=str(query) if query else None,
                        url=str(url) if url else None,
                        new_window=new_window,
                    )
                    profile = launch["profile"]
                    result["success"] = True
                    result["data"] = launch
                    result["message"] = (
                        f"Chrome abierto con perfil {profile['display_name']} ({profile['dir_name']})"
                    )

                case "chrome_open_automation_profile":
                    profile_name = str(action.params.get("profile_name", "chrome-agent-profile"))
                    url = action.params.get("url")
                    new_window = _coerce_bool(action.params.get("new_window", True), default=True)
                    launch = self._auto.open_chrome_automation_profile(
                        profile_name=profile_name,
                        url=str(url) if url else None,
                        new_window=new_window,
                    )
                    result["success"] = True
                    result["data"] = launch
                    result["message"] = (
                        f"Chrome abierto con perfil de automatizacion en {launch['profile_dir']}"
                    )

                case "browser_use_profile":
                    if not self._browser or not self._browser.is_available():
                        result["message"] = "Browser automation no disponible: no hay backend estructurado de navegador"
                        return result
                    query = action.params.get("query")
                    data = await self._browser.ensure_human_profile(
                        query=str(query) if query else None,
                        headless=_coerce_bool(action.params.get("headless", False)),
                    )
                    result["data"] = data
                    profile = data.get("profile") or {}
                    profile_name = profile.get("display_name") or data.get("profile_ref")
                    extension_connected = bool(data.get("extension_connected", data.get("connection") == "extension"))
                    if not extension_connected:
                        result["success"] = False
                        result["message"] = (
                            f"Chrome se abrio con el perfil humano {profile_name}, pero la extension G-Mini Agent Bridge no conecto. "
                            "Replanifica con fallback de escritorio/computer use o instala la extension en ese perfil."
                        )
                        return result
                    result["success"] = True
                    result["message"] = f"Browser conectado al perfil humano {profile_name}"

                case "browser_use_automation_profile":
                    if not self._browser or not self._browser.is_available():
                        result["message"] = "Browser automation no disponible: no hay backend estructurado de navegador"
                        return result
                    profile_name = str(action.params.get("profile_name", "chrome-agent-profile"))
                    try:
                        data = await self._browser.ensure_automation_profile(
                            profile_name=profile_name,
                            headless=_coerce_bool(action.params.get("headless", False)),
                        )
                    except RuntimeError as exc:
                        result["success"] = False
                        result["data"] = {
                            "recovery_hint": self._browser.build_desktop_fallback_hint(
                                profile_query=profile_name,
                                profile_ref=profile_name,
                                automation=True,
                                issue=str(exc),
                            )
                        }
                        result["message"] = (
                            f"{exc} Replanifica con Chrome de escritorio + screenshot + acciones de PC, "
                            "o instala/conecta la extension para ese perfil."
                        )
                        return result
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Browser conectado al perfil de automatizacion {profile_name}"

                case "browser_navigate":
                    url = str(action.params.get("url", ""))

                    # ── Bloqueo de sitios baneados (configurable) — check before any backend ──
                    blocked_enabled, blocked_sites = _get_blocked_sites_config()
                    if blocked_enabled and blocked_sites:
                        from urllib.parse import urlparse
                        try:
                            parsed_host = urlparse(url).hostname or ""
                            for blocked_site in blocked_sites:
                                if blocked_site in parsed_host.lower():
                                    result["success"] = False
                                    result["message"] = (
                                        f"Navegacion bloqueada por configuracion del usuario: {blocked_site}. "
                                        "Puedes cambiar esta politica desde Settings si quieres permitir este dominio."
                                    )
                                    logger.warning(
                                        f"Navegacion bloqueada a sitio configurado: {url} (regla: {blocked_site})"
                                    )
                                    return result
                        except Exception:
                            pass

                    # ── Priority: 1) Extension  2) browser-use  3) error with CU instructions ──
                    _extension_ready = await self._ensure_browser_session()
                    if _extension_ready:
                        data = await self._browser.navigate(url)
                        result["success"] = True
                        result["data"] = data
                        result["message"] = f"Navegado a {data['url']}"
                    else:
                        logger.info(f"[Planner] Extensión no disponible, intentando browser-use para: {url}")
                        _bu = await self._get_browseruse()
                        if _bu:
                            try:
                                data = await _bu.navigate(url)
                                result["success"] = True
                                result["data"] = data
                                result["message"] = f"Navegado via browser-use a {data.get('url', url)}"
                            except Exception as _bu_err:
                                result["message"] = (
                                    f"browser-use falló: {_bu_err}. "
                                    "Usa computer use: click barra de direcciones Chrome (x=centro, y≈55) → "
                                    "hotkey ctrl+a → type URL completa → press enter."
                                )
                        else:
                            result["message"] = (
                                "Extensión desconectada y browser-use no instalado. "
                                "Usa computer use: click barra de direcciones Chrome (x=centro, y≈55) → "
                                "hotkey ctrl+a → type URL completa → press enter."
                            )

                case "browser_click":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    selector = str(action.params.get("selector", ""))
                    force = _coerce_bool(action.params.get("force", False))
                    data = await self._browser.click(selector, force=force)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Click en selector {selector}" + (" (force)" if force else "")

                case "browser_force_click":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    selector = str(action.params.get("selector", ""))
                    data = await self._browser.click(selector, force=True)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Force click en selector {selector} (bypass overlays)"

                case "browser_remove_overlays":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    data = await self._browser.remove_overlays()
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Overlays eliminados: {data['removed_elements']} elementos removidos"

                case "browser_check_downloads":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    expected_ext = action.params.get("expected_ext")
                    filename_contains = action.params.get("filename_contains")
                    recency = int(action.params.get("recency_seconds", 300))
                    data = await self._browser.check_downloads_folder(
                        expected_ext=str(expected_ext) if expected_ext else None,
                        filename_contains=str(filename_contains) if filename_contains else None,
                        recency_seconds=recency,
                    )
                    result["success"] = data.get("found", False)
                    result["data"] = data
                    if data.get("found"):
                        files_info = ", ".join(f"{f['name']} ({f['size_bytes']}B)" for f in data['files'][:3])
                        result["message"] = f"Descargas encontradas: {files_info}"
                    else:
                        result["message"] = "No se encontraron descargas recientes que coincidan"

                case "browser_type":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    selector = str(action.params.get("selector", ""))
                    input_text = str(action.params.get("text", action.params.get("value", "")))
                    data = await self._browser.type(
                        selector,
                        input_text,
                        clear=_coerce_bool(action.params.get("clear", True), default=True),
                    )
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Texto escrito en selector {selector}"

                case "browser_press":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    key = str(action.params.get("key", "Enter"))
                    data = await self._browser.press(key)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Tecla enviada al browser: {key}"

                case "browser_extract":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    selector = str(action.params.get("selector", "body"))
                    instruction = action.params.get("instruction")
                    data = await self._browser.extract(selector)
                    result["success"] = True
                    result["data"] = data
                    if instruction:
                        result["message"] = f"Contenido extraido de {selector} para: {instruction}"
                    else:
                        result["message"] = f"Contenido extraido de {selector}"

                case "browser_snapshot":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    data = await self._browser.snapshot()
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Snapshot del browser en {data['url']}"

                case "browser_eval":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    script = str(action.params.get("script", ""))
                    data = await self._browser.evaluate(script)
                    result["success"] = True
                    result["data"] = data
                    preview = data.get("result")
                    if preview is None:
                        result["message"] = "Script ejecutado en browser"
                    else:
                        preview_text = str(preview)
                        if len(preview_text) > 180:
                            preview_text = preview_text[:177] + "..."
                        result["message"] = f"Script ejecutado en browser: {preview_text}"

                case "browser_screenshot":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    data = await self._browser.screenshot()
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Screenshot del browser en {data['url']}"

                case "browser_state":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    data = await self._browser.current_state()
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Browser activo en {data['url']}"

                case "browser_scroll":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    direction = str(action.params.get("direction", "down"))
                    amount = int(action.params.get("amount", 3))
                    data = await self._browser.scroll(direction=direction, amount=amount)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Scroll {direction} x{amount} en browser"

                case "browser_wait_for":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    selector = str(action.params.get("selector", ""))
                    timeout_ms = int(action.params.get("timeout_ms", 15000))
                    state = str(action.params.get("state", "visible"))
                    data = await self._browser.wait_for_selector(selector, timeout_ms=timeout_ms, state=state)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Elemento {selector} encontrado ({state})"

                case "browser_wait_load":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    state = str(action.params.get("state", "domcontentloaded"))
                    data = await self._browser.wait_for_load(state=state)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Pagina cargada ({state})"

                case "browser_go_back":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    data = await self._browser.go_back()
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Navegado atras: {data['url']}"

                case "browser_go_forward":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    data = await self._browser.go_forward()
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Navegado adelante: {data['url']}"

                case "browser_tabs":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    data = await self._browser.list_tabs()
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Tabs abiertas: {data['count']}"

                case "browser_switch_tab":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    index = int(action.params.get("index", 0))
                    data = await self._browser.switch_tab(index)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Cambiado a tab {index}: {data['url']}"

                case "browser_close_tab":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    index = action.params.get("index")
                    data = await self._browser.close_tab(index=int(index) if index is not None else None)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Tab cerrada. Restantes: {data['remaining_tabs']}"

                case "browser_new_tab":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    url = action.params.get("url")
                    data = await self._browser.new_tab(url=str(url) if url else None)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Nueva tab abierta: {data['url']}"

                case "browser_hover":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    selector = str(action.params.get("selector", ""))
                    data = await self._browser.hover(selector)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Hover en selector {selector}"

                case "browser_select":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    selector = str(action.params.get("selector", ""))
                    value = str(action.params.get("value", ""))
                    data = await self._browser.select_option(selector, value)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Opcion seleccionada: {value}"

                case "browser_fill":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    selector = str(action.params.get("selector", ""))
                    input_text = str(action.params.get("text", action.params.get("value", "")))
                    data = await self._browser.fill(selector, input_text)
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Campo rellenado: {selector}"

                case "browser_page_info":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    data = await self._browser.get_page_info()
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Pagina: {data['title']} ({data['url']})"

                case "browser_download_click":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    selector = str(action.params.get("selector", ""))
                    timeout_ms = int(action.params.get("timeout_ms", 30000))
                    expected_kind = str(action.params.get("expected_kind", "video"))
                    data = await self._browser.click_and_wait_for_download(
                        selector=selector,
                        timeout_ms=timeout_ms,
                        expected_kind=expected_kind,
                    )
                    result["success"] = bool(data.get("exists")) and bool(data.get("approved_for_use"))
                    result["data"] = data
                    if data.get("approved_for_use"):
                        result["message"] = f"Descarga confirmada y validada: {data['filename']}"
                    else:
                        vt = data.get("virustotal", {})
                        result["message"] = (
                            f"Descarga bloqueada o no confiable: {data['filename']} | VT={vt.get('status', 'unknown')}"
                        )

                case "browser_list_downloads":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    limit = int(action.params.get("limit", 20))
                    data = {"downloads": self._browser.list_downloads(limit=limit)}
                    result["success"] = True
                    result["data"] = data
                    result["message"] = f"Descargas registradas: {len(data['downloads'])}"

                case "browser_scan_file":
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    file_path = str(action.params.get("path", ""))
                    data = await self._browser.scan_file_with_virustotal(file_path)
                    result["success"] = bool(data.get("trusted"))
                    result["data"] = data
                    if data.get("trusted"):
                        result["message"] = f"Archivo validado por VirusTotal: {file_path}"
                    else:
                        result["message"] = f"Archivo no confiable o sin validar en VirusTotal: {file_path}"

                case "wait":
                    seconds = float(action.params.get("seconds", 1))
                    await _emit_action_event("wait", {"seconds": seconds})
                    await asyncio.sleep(seconds)
                    result["success"] = True
                    result["message"] = f"Esperado {seconds}s"

                case "move":
                    x = int(action.params.get("x", 0))
                    y = int(action.params.get("y", 0))
                    x, y = self._scale_coordinates(x, y)
                    await _emit_action_event("move", {"x": x, "y": y})
                    ok = await self._auto.move_to(x, y)
                    result["success"] = ok
                    result["message"] = f"Mouse movido a ({x}, {y})" if ok else "Mover mouse falló"

                case "drag":
                    x = int(action.params.get("x", 0))
                    y = int(action.params.get("y", 0))
                    ok = await self._auto.drag_to(x, y)
                    result["success"] = ok
                    result["message"] = f"Drag a ({x}, {y})" if ok else "Drag falló"

                # ── Android Actions ────
                case "adb_tap":
                    x = int(action.params.get("x", 0))
                    y = int(action.params.get("y", 0))
                    ok = await self._adb.tap(x, y)
                    result["success"] = ok

                case "adb_swipe":
                    x1 = int(action.params.get("x1", 0))
                    y1 = int(action.params.get("y1", 0))
                    x2 = int(action.params.get("x2", 0))
                    y2 = int(action.params.get("y2", 0))
                    ok = await self._adb.swipe(x1, y1, x2, y2)
                    result["success"] = ok

                case "adb_text":
                    text = str(action.params.get("text", ""))
                    ok = await self._adb.input_text(text)
                    result["success"] = ok

                # ── Media Generation (Imagen, Veo, Lyria) ────
                case "generate_image":
                    from backend.providers.google_media import generate_image as _gen_image
                    prompt = str(action.params.get("prompt", ""))
                    aspect_ratio = str(action.params.get("aspect_ratio", "1:1"))
                    model = action.params.get("model") or None
                    data = await _gen_image(prompt=prompt, model=model, aspect_ratio=aspect_ratio)
                    result["success"] = data.get("success", False) or bool(data.get("files"))
                    result["data"] = data
                    result["message"] = data.get("message", "Imagen generada" if result["success"] else "Error generando imagen")

                case "generate_video":
                    from backend.providers.google_media import generate_video as _gen_video
                    prompt = str(action.params.get("prompt", ""))
                    model = action.params.get("model") or None
                    duration = action.params.get("duration_seconds")
                    data = await _gen_video(prompt=prompt, model=model, duration_seconds=duration)
                    result["success"] = data.get("success", False) or bool(data.get("files"))
                    result["data"] = data
                    result["message"] = data.get("message", "Video generado" if result["success"] else "Error generando video")

                case "generate_music":
                    from backend.providers.google_media import generate_music as _gen_music
                    prompt = str(action.params.get("prompt", ""))
                    model = action.params.get("model") or None
                    data = await _gen_music(prompt=prompt, model=model)
                    result["success"] = data.get("success", False) or bool(data.get("files"))
                    result["data"] = data
                    result["message"] = data.get("message", "Música generada" if result["success"] else "Error generando música")

                # ── Task Control ────
                case "task_complete":
                    summary = str(action.params.get("summary", "Tarea completada"))
                    result["success"] = True
                    result["message"] = summary
                    result["task_complete"] = True  # Flag especial para el loop

                case "browser_desktop_fallback_click":
                    # Fallback: toma un screenshot del browser, luego hace click en coordenadas
                    # de escritorio usando pyautogui. Útil cuando selectores CSS fallan.
                    if not await self._ensure_browser_session():
                        result["message"] = "Browser automation no disponible: no se pudo inicializar sesión de browser"
                        return result
                    x = int(action.params.get("x", 0))
                    y = int(action.params.get("y", 0))
                    description = str(action.params.get("description", "fallback click"))
                    # Primero traer el browser al frente evaluando window.focus()
                    try:
                        await self._browser.evaluate("window.focus()")
                    except Exception:
                        pass
                    import asyncio as _aio
                    await _aio.sleep(0.3)
                    # Ahora hacer click de escritorio en las coordenadas dadas
                    x_scaled, y_scaled = self._scale_coordinates(x, y)
                    await _emit_action_event("click", {"x": x_scaled, "y": y_scaled})
                    ok = await self._auto.click(x_scaled, y_scaled)
                    result["success"] = ok
                    result["data"] = {"x": x_scaled, "y": y_scaled, "description": description}
                    result["message"] = (
                        f"Desktop fallback click en ({x_scaled}, {y_scaled}) — {description}"
                    )

                case "open_application":
                    import subprocess as _subproc
                    app_name = str(action.params.get("name", "") or action.params.get("app", "")).strip()
                    if not app_name:
                        result["message"] = "Falta el nombre de la aplicación en open_application"
                        return result
                    try:
                        _subproc.Popen(
                            ["cmd", "/c", "start", "", app_name],
                            shell=False,
                            stdout=_subproc.DEVNULL,
                            stderr=_subproc.DEVNULL,
                        )
                        result["success"] = True
                        result["message"] = f"Aplicación '{app_name}' abierta"
                    except Exception as app_exc:
                        result["message"] = f"No se pudo abrir '{app_name}': {app_exc}"

                case _:
                    result["message"] = f"Acción desconocida: {action.type}"
                    logger.warning(f"Acción no reconocida: {action.type}")

        except Exception as e:
            result["message"] = f"Error: {str(e)}"
            logger.error(f"Error ejecutando {action.type}: {e}")

        return result
