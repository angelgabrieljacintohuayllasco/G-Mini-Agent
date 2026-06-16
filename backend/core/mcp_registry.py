"""
G-Mini Agent - MCP Server Registry.
Registro de servidores MCP. Carga automáticamente desde config (mcp.servers).
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

from backend.config import config

_logger = logging.getLogger(__name__)

VALID_STDIO_TRANSPORTS = ["stdio"]


def _resolve_command(command: str) -> str | None:
    """Resuelve el ejecutable real (npx → npx.cmd en Windows)."""
    if not command:
        return None
    if sys.platform == "win32" and not Path(command).suffix:
        for ext in (".cmd", ".bat", ".exe"):
            resolved = shutil.which(command + ext)
            if resolved:
                return resolved
    resolved = shutil.which(command)
    return resolved or None


def _build_server_entry(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza un dict de config a la forma que MCPRuntime espera."""
    server_id = str(raw.get("id") or raw.get("name") or "").strip()
    if not server_id:
        return {}

    command = str(raw.get("command") or "").strip()
    transport = str(raw.get("transport") or "stdio").strip().lower()
    enabled = bool(raw.get("enabled", True))

    resolved = _resolve_command(command) if command else None
    ready = enabled and bool(resolved) and transport in VALID_STDIO_TRANSPORTS

    status = "disabled"
    detail = ""
    if not enabled:
        status = "disabled"
        detail = "Servidor desactivado por el usuario."
    elif not command:
        status = "error"
        detail = "Falta el comando del servidor."
    elif not resolved:
        status = "pending"
        detail = f"Comando '{command}' no encontrado en PATH."
        ready = False
    elif transport not in VALID_STDIO_TRANSPORTS:
        status = "error"
        detail = f"Transporte '{transport}' no soportado."
    else:
        status = "ready"

    args_raw = raw.get("args") or []
    if isinstance(args_raw, str):
        args_raw = args_raw.split() if args_raw.strip() else []
    args = [str(a) for a in args_raw]

    env_raw = raw.get("env") or {}
    env = {str(k): str(v) for k, v in env_raw.items()} if isinstance(env_raw, dict) else {}

    return {
        "id": server_id,
        "name": str(raw.get("name") or server_id).strip(),
        "command": command,
        "resolved_command": resolved,
        "args": args,
        "transport": transport,
        "enabled": enabled,
        "ready": ready,
        "status": status,
        "detail": detail,
        "cwd": str(raw.get("cwd") or "").strip() or None,
        "env": env,
    }


class MCPRegistry:
    """Registro de servidores MCP. Carga automáticamente desde config."""

    def __init__(self):
        self._servers: Dict[str, Dict[str, Any]] = {}
        self._enabled = False
        self._load_from_config()

    def _load_from_config(self) -> None:
        """Lee mcp.enabled y mcp.servers desde config y registra todos."""
        self._enabled = bool(config.get("mcp", "enabled", default=True))
        raw_servers = config.get("mcp", "servers", default=[])
        if not isinstance(raw_servers, list):
            raw_servers = []

        self._servers.clear()
        for raw in raw_servers:
            if not isinstance(raw, dict):
                continue
            entry = _build_server_entry(raw)
            if not entry or not entry.get("id"):
                continue
            if not self._enabled:
                entry["ready"] = False
                entry["status"] = "globally_disabled"
                entry["detail"] = "MCP desactivado globalmente."
            self._servers[entry["id"]] = entry

        ready_count = sum(1 for s in self._servers.values() if s.get("ready"))
        _logger.info(
            "MCPRegistry cargado: enabled=%s, %d servidor(es), %d listo(s)",
            self._enabled, len(self._servers), ready_count,
        )

    def reload(self) -> None:
        """Recarga servidores desde config (útil después de cambios en settings)."""
        self._load_from_config()

    def list_servers(self) -> Dict[str, Any]:
        """Lista todos los servidores registrados."""
        return {
            "enabled": self._enabled,
            "servers": list(self._servers.values()),
        }

    def get_server(self, server_id: str) -> Dict[str, Any]:
        """Obtiene servidor por ID. Lanza KeyError si no existe."""
        server = self._servers.get(server_id)
        if server is None:
            raise KeyError(f"Servidor MCP '{server_id}' no encontrado.")
        return server

    def get_runtime_server(self, server_id: str) -> Dict[str, Any]:
        """Obtiene config runtime-validado del servidor."""
        server = self.get_server(server_id)
        return server

    def register_server(self, server_config: Dict[str, Any]) -> str:
        """Registra/agrega servidor en memoria."""
        entry = _build_server_entry(server_config)
        if not entry or not entry.get("id"):
            raise ValueError("Configuración de servidor MCP inválida: falta id o name.")
        if not self._enabled:
            entry["ready"] = False
            entry["status"] = "globally_disabled"
            entry["detail"] = "MCP desactivado globalmente."
        self._servers[entry["id"]] = entry
        return entry["id"]

    def remove_server(self, server_id: str) -> bool:
        """Elimina un servidor del registro."""
        return self._servers.pop(server_id, None) is not None

    def enable(self, enabled: bool = True) -> None:
        """Habilita/deshabilita el registry y actualiza estado de servidores."""
        self._enabled = enabled
        for server in self._servers.values():
            if enabled:
                command = server.get("command", "")
                resolved = server.get("resolved_command")
                transport = server.get("transport", "stdio")
                srv_enabled = server.get("enabled", True)
                server["ready"] = (
                    srv_enabled
                    and bool(resolved)
                    and transport in VALID_STDIO_TRANSPORTS
                )
                if server["ready"]:
                    server["status"] = "ready"
                    server["detail"] = ""
            else:
                server["ready"] = False
                server["status"] = "globally_disabled"
                server["detail"] = "MCP desactivado globalmente."

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def ready_count(self) -> int:
        return sum(1 for s in self._servers.values() if s.get("ready"))


_registry_instance: MCPRegistry | None = None


def get_mcp_registry() -> MCPRegistry:
    """Singleton global del registry."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = MCPRegistry()
    return _registry_instance


def reset_mcp_registry() -> None:
    """Resetea el singleton (útil para reload de config)."""
    global _registry_instance
    _registry_instance = None
