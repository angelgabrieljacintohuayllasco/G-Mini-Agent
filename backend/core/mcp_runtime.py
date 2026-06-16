"""
G-Mini Agent - MCP runtime.
Cliente base para servidores MCP configurados, con soporte actual para stdio.
Arquitectura: sesiones persistentes con pool, caché de tools, reconexión automática.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from backend.config import config
from backend.core.mcp_registry import MCPRegistry, VALID_STDIO_TRANSPORTS

DEFAULT_PROTOCOL_VERSION = "2024-11-05"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
TOOLS_CACHE_TTL_SECONDS = 300  # 5 minutos
GRACEFUL_SHUTDOWN_SECONDS = 5

_logger = logging.getLogger(__name__)


class _MCPProcessSession:
    """Sesión persistente contra un servidor MCP vía stdio/JSON-RPC 2.0."""

    def __init__(self, server: dict[str, Any], timeout_seconds: int):
        self.server = server
        self.timeout_seconds = timeout_seconds
        self.process: subprocess.Popen[str] | None = None
        self._stdout_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_lines: list[str] = []
        self._notifications: list[dict[str, Any]] = []
        self._request_id = 0
        self.protocol_version = ""
        self._initialized = False
        self._lock = threading.Lock()

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def initialized(self) -> bool:
        return self._initialized and self.alive

    @property
    def stderr_output(self) -> str:
        return "\n".join(self._stderr_lines[-50:]).strip()

    @property
    def notifications(self) -> list[dict[str, Any]]:
        return list(self._notifications)

    def start(self) -> None:
        """Inicia el proceso MCP heredando el entorno completo del host."""
        command = [
            str(self.server["resolved_command"] or self.server["command"]),
            *self.server.get("args", []),
        ]
        cwd = self.server.get("cwd")

        # Heredar TODO el entorno del host (patrón vscode-copilot)
        # y superponer las variables específicas del servidor
        env = {**os.environ, **self.server.get("env", {})}
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        # Windows: asegurar que npx → npx.cmd se resuelva
        resolved_cmd = command[0]
        if sys.platform == "win32" and not Path(resolved_cmd).suffix:
            for ext in (".cmd", ".bat", ".exe"):
                candidate = shutil.which(resolved_cmd + ext)
                if candidate:
                    command[0] = candidate
                    break

        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        threading.Thread(
            target=self._stdout_loop,
            name=f"mcp-stdout-{self.server['id']}",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._stderr_loop,
            name=f"mcp-stderr-{self.server['id']}",
            daemon=True,
        ).start()

    def close(self) -> None:
        """Cierre graceful: SIGTERM → esperar → SIGKILL (patrón vscode)."""
        if self.process is None:
            return
        self._initialized = False
        if self.process.stdin:
            try:
                self.process.stdin.close()
            except Exception:
                pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=GRACEFUL_SHUTDOWN_SECONDS)
            except subprocess.TimeoutExpired:
                self.process.kill()
                try:
                    self.process.wait(timeout=3)
                except Exception:
                    pass
        self.process = None

    def initialize(self) -> dict[str, Any]:
        """Handshake MCP: initialize + notifications/initialized."""
        if self._initialized:
            return {"result": {"protocolVersion": self.protocol_version}}
        response = self.request(
            "initialize",
            {
                "protocolVersion": str(
                    config.get("mcp", "protocol_version", default=DEFAULT_PROTOCOL_VERSION)
                ),
                "capabilities": {},
                "clientInfo": {
                    "name": "G-Mini Agent",
                    "version": str(config.get("app", "version", default="0.1.0")),
                },
            },
        )
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        self.protocol_version = str(result.get("protocolVersion") or "")
        self.notify("notifications/initialized", {})
        self._initialized = True
        return response

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        return self._wait_for_response(request_id)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
            }
        )

    def _send(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("La sesion MCP no esta iniciada.")
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _wait_for_response(self, request_id: int) -> dict[str, Any]:
        deadline = time.time() + self.timeout_seconds
        while time.time() < deadline:
            remaining = max(0.05, deadline - time.time())
            try:
                message = self._stdout_queue.get(timeout=remaining)
            except queue.Empty:
                if self.process is not None and self.process.poll() is not None:
                    raise RuntimeError(
                        "El servidor MCP finalizo antes de responder. "
                        + (self.stderr_output or "Sin detalles en stderr.")
                    )
                continue

            if message.get("id") == request_id:
                if "error" in message:
                    error = message.get("error") or {}
                    raise RuntimeError(
                        f"MCP {self.server['id']} devolvio error {error.get('code')}: {error.get('message')}"
                    )
                return message

            if message.get("method"):
                self._notifications.append(message)

        raise TimeoutError(
            f"Timeout esperando respuesta MCP de {self.server['id']} tras {self.timeout_seconds}s."
        )

    def _stdout_loop(self) -> None:
        if self.process is None or self.process.stdout is None:
            return
        for raw_line in self.process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                self._stdout_queue.put(
                    {
                        "jsonrpc": "2.0",
                        "method": "_invalid",
                        "params": {"raw": line},
                    }
                )
                continue
            if isinstance(payload, dict):
                self._stdout_queue.put(payload)

    def _stderr_loop(self) -> None:
        if self.process is None or self.process.stderr is None:
            return
        for raw_line in self.process.stderr:
            line = raw_line.rstrip()
            if line:
                self._stderr_lines.append(line)


class MCPSessionPool:
    """Pool de sesiones persistentes — mantiene procesos MCP vivos y los reutiliza."""

    def __init__(self):
        self._sessions: dict[str, _MCPProcessSession] = {}
        self._lock = threading.Lock()

    def get_or_create(
        self, server: dict[str, Any], timeout_seconds: int
    ) -> _MCPProcessSession:
        """Obtiene una sesión existente y sana, o crea una nueva."""
        server_id = server["id"]
        with self._lock:
            session = self._sessions.get(server_id)
            if session and session.alive:
                session.timeout_seconds = timeout_seconds
                return session
            # Sesión muerta — limpiar
            if session:
                _logger.info("MCP session %s muerta, reconectando...", server_id)
                try:
                    session.close()
                except Exception:
                    pass
            # Crear nueva
            _logger.info("Creando sesión MCP persistente para '%s'", server_id)
            new_session = _MCPProcessSession(server, timeout_seconds)
            new_session.start()
            self._sessions[server_id] = new_session
            return new_session

    def remove(self, server_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(server_id, None)
        if session:
            session.close()

    def health_check(self, server_id: str) -> bool:
        """Verifica si la sesión está viva."""
        with self._lock:
            session = self._sessions.get(server_id)
        return session is not None and session.alive

    def shutdown_all(self) -> None:
        """Cierra todas las sesiones de forma ordenada."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception as exc:
                _logger.warning("Error cerrando sesión MCP %s: %s", session.server.get("id", "?"), exc)
        _logger.info("MCPSessionPool cerrado (%d sesiones)", len(sessions))


class _ToolsCacheEntry:
    """Entrada del caché de tools con TTL."""

    __slots__ = ("tools", "timestamp")

    def __init__(self, tools: list[dict[str, Any]]):
        self.tools = tools
        self.timestamp = time.time()

    def expired(self, ttl: float = TOOLS_CACHE_TTL_SECONDS) -> bool:
        return (time.time() - self.timestamp) > ttl


class MCPRuntime:
    """Runtime MCP con sesiones persistentes, caché de tools y auto-discovery."""

    def __init__(self, registry: MCPRegistry | None = None):
        self._registry = registry or MCPRegistry()
        self._pool = MCPSessionPool()
        self._tools_cache: dict[str, _ToolsCacheEntry] = {}
        self._cache_lock = threading.Lock()

    @property
    def pool(self) -> MCPSessionPool:
        return self._pool

    def list_tools(
        self, server_id: str, cursor: str | None = None, timeout_seconds: int | None = None
    ) -> dict[str, Any]:
        server = self._prepare_server(server_id)
        session = self._pool.get_or_create(server, self._resolve_timeout(timeout_seconds))

        # Inicializar si es necesario
        if not session.initialized:
            session.initialize()

        params = {"cursor": cursor} if cursor else {}
        response = session.request("tools/list", params)
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        tools = result.get("tools", [])

        # Actualizar caché
        with self._cache_lock:
            self._tools_cache[server_id] = _ToolsCacheEntry(tools)

        return {
            "success": True,
            "server_id": server["id"],
            "server_name": server["name"],
            "transport": server["transport"],
            "protocol_version": session.protocol_version,
            "tools": tools,
            "next_cursor": result.get("nextCursor"),
            "stderr": session.stderr_output,
            "notifications": session.notifications,
            "error": None,
        }

    def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        _logger.info(
            "MCP call_tool: server_id=%s, tool=%s, arguments=%s",
            server_id, tool_name, arguments,
        )
        server = self._prepare_server(server_id)
        requested_tool = str(tool_name or "").strip()
        if not requested_tool:
            raise ValueError("Falta tool para llamar el servidor MCP.")

        session = self._pool.get_or_create(server, self._resolve_timeout(timeout_seconds))

        # Inicializar si es necesario
        if not session.initialized:
            session.initialize()

        # Validar que la tool existe en el servidor antes de llamarla
        cached_tools = self.get_cached_tools(server_id)
        if cached_tools is not None:
            known_names = [t.get("name", "") for t in cached_tools]
            if requested_tool not in known_names:
                return {
                    "success": False,
                    "server_id": server["id"],
                    "server_name": server["name"],
                    "transport": server.get("transport", ""),
                    "tool": requested_tool,
                    "is_error": True,
                    "content": [],
                    "structured_content": None,
                    "raw_result": {},
                    "stderr": "",
                    "notifications": [],
                    "error": (
                        f"Tool '{requested_tool}' NO existe en servidor '{server_id}'. "
                        f"Tools disponibles: {', '.join(known_names)}"
                    ),
                }

        response = session.request(
            "tools/call",
            {
                "name": requested_tool,
                "arguments": dict(arguments or {}),
            },
        )
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        call_result = {
            "success": not bool(result.get("isError", False)),
            "server_id": server["id"],
            "server_name": server["name"],
            "transport": server["transport"],
            "protocol_version": session.protocol_version,
            "tool": requested_tool,
            "is_error": bool(result.get("isError", False)),
            "content": result.get("content", []),
            "structured_content": result.get("structuredContent"),
            "raw_result": result,
            "stderr": session.stderr_output,
            "notifications": session.notifications,
            "error": None,
        }
        _logger.info(
            "MCP call_tool result: server=%s tool=%s success=%s is_error=%s content_items=%d",
            server_id, requested_tool, call_result["success"],
            call_result["is_error"], len(call_result["content"]),
        )
        if call_result["stderr"]:
            _logger.debug("MCP call_tool stderr: %s", call_result["stderr"][:500])
        return call_result

    def get_cached_tools(self, server_id: str) -> list[dict[str, Any]] | None:
        """Devuelve tools cacheadas si existen y no expiraron."""
        with self._cache_lock:
            entry = self._tools_cache.get(server_id)
            if entry and not entry.expired():
                return entry.tools
        return None

    def invalidate_cache(self, server_id: str | None = None) -> None:
        """Invalida caché de tools para un servidor o todos."""
        with self._cache_lock:
            if server_id:
                self._tools_cache.pop(server_id, None)
            else:
                self._tools_cache.clear()

    def discover_all_tools(self, timeout_seconds: int | None = None) -> dict[str, list[dict[str, Any]]]:
        """Auto-discovery: lista tools de TODOS los servidores ready.
        Devuelve {server_id: [tool_info, ...]}"""
        result: dict[str, list[dict[str, Any]]] = {}
        servers_data = self._registry.list_servers()
        if not servers_data.get("enabled"):
            return result

        for server in servers_data.get("servers", []):
            if not server.get("ready"):
                continue
            sid = server["id"]

            # Usar caché si está fresco
            cached = self.get_cached_tools(sid)
            if cached is not None:
                result[sid] = cached
                continue

            try:
                data = self.list_tools(sid, timeout_seconds=timeout_seconds)
                if data.get("success"):
                    result[sid] = data.get("tools", [])
            except Exception as exc:
                _logger.warning("Auto-discovery falló para %s: %s", sid, exc)
                result[sid] = []

        return result

    def get_all_tools_summary(self, timeout_seconds: int | None = None) -> str:
        """Genera un resumen legible de todas las tools MCP disponibles para inyectar en contexto del LLM."""
        all_tools = self.discover_all_tools(timeout_seconds=timeout_seconds)
        if not all_tools:
            return ""

        lines: list[str] = []
        for server_id, tools in all_tools.items():
            if not tools:
                continue
            lines.append(f"\n### Servidor MCP: `{server_id}`")
            for tool in tools:
                name = tool.get("name", "?")
                desc = tool.get("description", "Sin descripción")
                schema = tool.get("inputSchema", {})
                props = schema.get("properties", {})
                required = schema.get("required", [])

                params_parts: list[str] = []
                for pname, pinfo in props.items():
                    ptype = pinfo.get("type", "any")
                    req_mark = " (requerido)" if pname in required else ""
                    pdesc = pinfo.get("description", "")
                    params_parts.append(f"  - `{pname}` ({ptype}{req_mark}): {pdesc}")

                lines.append(f"- **{name}**: {desc}")
                if params_parts:
                    lines.append("  Parámetros:")
                    lines.extend(params_parts)

        if not lines:
            return ""
        return "\n".join(lines)

    def shutdown(self) -> None:
        """Cierra el pool y limpia caché."""
        self._pool.shutdown_all()
        self.invalidate_cache()

    def _prepare_server(self, server_id: str) -> dict[str, Any]:
        server = self._registry.get_runtime_server(server_id)
        if not server.get("ready"):
            raise RuntimeError(
                f"El servidor MCP '{server['id']}' no esta listo: {server.get('detail') or server.get('status')}"
            )
        if server["transport"] not in VALID_STDIO_TRANSPORTS:
            raise NotImplementedError(
                f"Transporte MCP no soportado todavia por el runtime: {server['transport']}"
            )
        return server

    def _resolve_timeout(self, requested_timeout: int | None) -> int:
        configured = config.get("mcp", "request_timeout_seconds", default=DEFAULT_REQUEST_TIMEOUT_SECONDS)
        try:
            configured_value = int(configured)
        except (TypeError, ValueError):
            configured_value = DEFAULT_REQUEST_TIMEOUT_SECONDS

        try:
            requested_value = int(requested_timeout) if requested_timeout is not None else None
        except (TypeError, ValueError):
            requested_value = None

        timeout = requested_value or configured_value or DEFAULT_REQUEST_TIMEOUT_SECONDS
        return max(3, min(timeout, 180))
