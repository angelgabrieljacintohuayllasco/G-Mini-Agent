"""
G-Mini Agent — Hooks antes y después de cada acción (configurados por el usuario).

    hooks:
      pre_action:
        - match: "terminal_run"          # nombre exacto, prefijo con * ("browser_*") o "*"
          command: ["python", "C:/scripts/revisar.py"]
          timeout_s: 10
      post_action:
        - match: "*"
          command: ["powershell", "-NoProfile", "-File", "C:/scripts/registro.ps1"]

El comando (una lista, sin shell) recibe por stdin un JSON
{"event": "pre_action"|"post_action", "action", "params", "result"?}.
En pre_action, salir con código 2 bloquea la acción y su stderr es el motivo;
cualquier otro error del hook se registra pero no bloquea. Los hooks solo se
definen en la config (el agente no puede escribirla).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from typing import Any

from loguru import logger

from backend.config import config

BLOCK_EXIT_CODE = 2
DEFAULT_TIMEOUT_S = 10


def _hooks(event: str, action_type: str) -> list[dict[str, Any]]:
    raw = config.get("hooks", event, default=[]) or []
    selected = []
    for hook in raw if isinstance(raw, list) else []:
        if not isinstance(hook, dict) or not isinstance(hook.get("command"), list) or not hook["command"]:
            continue
        pattern = str(hook.get("match") or "*")
        if pattern == "*" or pattern == action_type or (pattern.endswith("*") and action_type.startswith(pattern[:-1])):
            selected.append(hook)
    return selected


async def _run(hook: dict[str, Any], payload: dict[str, Any]) -> tuple[int | None, str]:
    command = [str(part) for part in hook["command"]]
    try:
        timeout = float(hook.get("timeout_s") or DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_S
    try:
        process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except OSError as exc:
        logger.warning(f"Hook {command[0]} no se pudo ejecutar: {exc}")
        return None, str(exc)
    try:
        _out, err = await asyncio.wait_for(
            process.communicate(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")), timeout
        )
    except asyncio.TimeoutError:
        process.kill()
        logger.warning(f"Hook {command[0]} superó {timeout:.0f} s")
        return None, "timeout"
    return process.returncode, err.decode("utf-8", errors="replace").strip()


async def run_pre(action_type: str, params: dict[str, Any]) -> str | None:
    """Motivo de bloqueo si algún hook pre_action sale con código 2; si no, None."""
    for hook in _hooks("pre_action", action_type):
        code, stderr = await _run(hook, {"event": "pre_action", "action": action_type, "params": params})
        if code == BLOCK_EXIT_CODE:
            return stderr or "bloqueada por un hook"
        if code not in (0, None):
            logger.warning(f"Hook pre_action devolvió {code}: {stderr[:200]}")
    return None


async def run_post(action_type: str, params: dict[str, Any], result: dict[str, Any]) -> None:
    for hook in _hooks("post_action", action_type):
        summary = {k: result.get(k) for k in ("success", "message") if k in result}
        code, stderr = await _run(hook, {"event": "post_action", "action": action_type, "params": params,
                                         "result": summary})
        if code not in (0, None):
            logger.warning(f"Hook post_action devolvió {code}: {stderr[:200]}")
