"""
G-Mini Agent — Integración con editores de código (VS Code, VS Code Insiders, Cursor).

Detecta los editores instalados y abre carpetas, archivos (en línea/columna) y
diffs a través de su CLI. Las rutas se resuelven con el WorkspaceManager.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from backend.config import config
from backend.core.workspace_manager import WorkspaceManager

WINDOWS_EDITOR_CANDIDATES: dict[str, dict[str, Any]] = {
    "vscode": {
        "name": "VS Code",
        "env_var": "VSCODE_EXECUTABLE",
        "cli_names": ["code", "code.cmd"],
        "paths": [
            "%LOCALAPPDATA%\\Programs\\Microsoft VS Code\\Code.exe",
            "%PROGRAMFILES%\\Microsoft VS Code\\Code.exe",
            "%PROGRAMFILES(X86)%\\Microsoft VS Code\\Code.exe",
        ],
    },
    "vscode-insiders": {
        "name": "VS Code Insiders",
        "env_var": "VSCODE_INSIDERS_EXECUTABLE",
        "cli_names": ["code-insiders", "code-insiders.cmd"],
        "paths": [
            "%LOCALAPPDATA%\\Programs\\Microsoft VS Code Insiders\\Code - Insiders.exe",
            "%PROGRAMFILES%\\Microsoft VS Code Insiders\\Code - Insiders.exe",
        ],
    },
    "cursor": {
        "name": "Cursor",
        "env_var": "CURSOR_EXECUTABLE",
        "cli_names": ["cursor", "cursor.cmd"],
        "paths": [
            "%LOCALAPPDATA%\\Programs\\Cursor\\Cursor.exe",
            "%PROGRAMFILES%\\Cursor\\Cursor.exe",
        ],
    },
}


class IDEManager:
    def __init__(self, workspace: WorkspaceManager) -> None:
        self._workspace = workspace

    def detect_editors(self) -> dict[str, Any]:
        preferred = str(config.get("editors", "preferred", default="vscode")).strip().lower() or "vscode"
        configured_paths = config.get("editors", "paths", default={}) or {}
        available: list[dict[str, Any]] = []

        for editor_key, metadata in WINDOWS_EDITOR_CANDIDATES.items():
            detected = self._detect_editor(editor_key, metadata, configured_paths)
            if not detected:
                continue
            available.append(detected)

        return {
            "preferred": preferred,
            "available": available,
            "count": len(available),
        }

    def open_workspace(
        self,
        path: str | None = None,
        *,
        editor_key: str | None = None,
        new_window: bool = False,
    ) -> dict[str, Any]:
        resolved = self._workspace.resolve_path(path)
        if not resolved.exists():
            raise FileNotFoundError(f"ruta no encontrada: {resolved}")
        editor = self._resolve_editor(editor_key)
        args = [editor["launch_path"]]
        if new_window:
            args.append("--new-window")
        args.append(str(resolved))
        process = subprocess.Popen(args, shell=False)
        return {
            "editor": editor["key"],
            "editor_name": editor["name"],
            "path": str(resolved),
            "pid": process.pid,
        }

    def open_file(
        self,
        path: str,
        *,
        line: int = 1,
        column: int = 1,
        editor_key: str | None = None,
        new_window: bool = False,
    ) -> dict[str, Any]:
        resolved = self._workspace.resolve_path(path)
        if not resolved.exists():
            raise FileNotFoundError(f"archivo no encontrado: {resolved}")
        editor = self._resolve_editor(editor_key)
        args = [editor["launch_path"]]
        if new_window:
            args.append("--new-window")
        args.extend(["--goto", f"{resolved}:{max(1, int(line))}:{max(1, int(column))}"])
        process = subprocess.Popen(args, shell=False)
        return {
            "editor": editor["key"],
            "editor_name": editor["name"],
            "path": str(resolved),
            "line": max(1, int(line)),
            "column": max(1, int(column)),
            "pid": process.pid,
        }

    def open_diff(
        self,
        left_path: str,
        right_path: str,
        *,
        editor_key: str | None = None,
        new_window: bool = False,
    ) -> dict[str, Any]:
        left_resolved = self._workspace.resolve_path(left_path)
        right_resolved = self._workspace.resolve_path(right_path)
        if not left_resolved.exists():
            raise FileNotFoundError(f"archivo izquierdo no encontrado: {left_resolved}")
        if not right_resolved.exists():
            raise FileNotFoundError(f"archivo derecho no encontrado: {right_resolved}")
        editor = self._resolve_editor(editor_key)
        args = [editor["launch_path"]]
        if new_window:
            args.append("--new-window")
        args.extend(["--diff", str(left_resolved), str(right_resolved)])
        process = subprocess.Popen(args, shell=False)
        return {
            "editor": editor["key"],
            "editor_name": editor["name"],
            "left_path": str(left_resolved),
            "right_path": str(right_resolved),
            "pid": process.pid,
        }

    def _resolve_editor(self, editor_key: str | None) -> dict[str, Any]:
        summary = self.detect_editors()
        preferred = str(editor_key or summary["preferred"]).strip().lower()
        available = summary["available"]
        if not available:
            raise RuntimeError("no se detecto ningun editor compatible")

        for editor in available:
            if editor["key"] == preferred:
                return editor

        return available[0]

    def _detect_editor(
        self,
        editor_key: str,
        metadata: dict[str, Any],
        configured_paths: dict[str, Any],
    ) -> dict[str, Any] | None:
        candidates: list[Path] = []
        configured_path = str(configured_paths.get(editor_key, "")).strip()
        if configured_path:
            candidates.append(Path(os.path.expandvars(configured_path)).expanduser())

        env_var = metadata.get("env_var")
        if env_var:
            env_path = str(os.environ.get(env_var, "")).strip()
            if env_path:
                candidates.append(Path(env_path).expanduser())

        for cli_name in metadata.get("cli_names", []):
            cli_path = shutil.which(cli_name)
            if not cli_path:
                continue
            cli_candidate = Path(cli_path)
            if cli_candidate.name.lower().endswith(".cmd"):
                # bin\code.cmd -> el ejecutable real vive un nivel arriba de bin\
                candidates.append(cli_candidate.parent.parent / "Code.exe")
                candidates.append(cli_candidate.parent.parent / "Code - Insiders.exe")
                candidates.append(cli_candidate.parent.parent / "Cursor.exe")
            candidates.append(cli_candidate)

        for raw_path in metadata.get("paths", []):
            candidates.append(Path(os.path.expandvars(raw_path)).expanduser())

        seen: set[str] = set()
        for candidate in candidates:
            normalized = str(candidate)
            if normalized.lower() in seen:
                continue
            seen.add(normalized.lower())
            if not candidate.exists():
                continue
            return {
                "key": editor_key,
                "name": metadata["name"],
                "launch_path": str(candidate),
            }

        return None
