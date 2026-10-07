"""
G-Mini Agent — Node Manager.
Registro, emparejamiento y ciclo de vida de nodos (dispositivos remotos).
Soporta PCs remotas, móviles y dispositivos IoT como extensiones del agente.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TYPE_CHECKING

import aiosqlite
from loguru import logger

from backend.config import ROOT_DIR, config

if TYPE_CHECKING:
    import socketio

DB_DIR = ROOT_DIR / "data"
DB_DIR.mkdir(exist_ok=True)
DEFAULT_DB_PATH = DB_DIR / "gateway.db"


# ── Enums ─────────────────────────────────────────────────────────

class NodeType(str, Enum):
    PC_WINDOWS = "pc_windows"
    PC_LINUX = "pc_linux"
    PC_MAC = "pc_mac"
    ANDROID = "android"
    IOS = "ios"
    IOT = "iot"
    CUSTOM = "custom"


class NodeStatus(str, Enum):
    PAIRING = "pairing"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    BANNED = "banned"


# ── Surfaces ──────────────────────────────────────────────────────

KNOWN_SURFACES = {
    "camera.capture", "camera.stream",
    "location.get", "location.track",
    "files.list", "files.read", "files.write", "files.delete", "files.sync",
    "exec.run", "exec.shell",
    "system.info", "system.shutdown", "system.restart", "system.sleep",
    "screen.capture", "screen.stream",
    "notifications.send", "notifications.list",
    "device.info", "device.battery",
    "voice.record", "voice.play",
    "clipboard.get", "clipboard.set",
    "contacts.list", "contacts.search",
    "sms.send", "sms.read",
    "sensors.list", "sensors.read",
}


# ── Data Classes ──────────────────────────────────────────────────

@dataclass
class NodeInfo:
    node_id: str
    name: str
    node_type: str
    status: str
    surfaces: list[str] = field(default_factory=list)
    permissions: dict[str, bool] = field(default_factory=dict)
    pairing_token: str | None = None
    pairing_expires_at: str | None = None
    ws_sid: str | None = None
    connected_at: str | None = None
    last_seen_at: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def has_surface(self, surface: str) -> bool:
        if surface in self.surfaces:
            return True
        prefix = surface.rsplit(".", 1)[0] + ".*"
        return prefix in self.surfaces

    def is_surface_allowed(self, surface: str) -> bool:
        return self.has_surface(surface) and self.permissions.get(surface, True)


# ── Node Manager ──────────────────────────────────────────────────

class NodeManager:
    """Gestiona el registro y ciclo de vida de nodos remotos."""

    def __init__(self, sio: socketio.AsyncServer | None = None):
        self._sio = sio
        self._db_path = self._resolve_db_path()
        self._nodes: dict[str, NodeInfo] = {}
        self._sid_to_node: dict[str, str] = {}
        self._pairing_tokens: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._initialized = False

    @staticmethod
    def _resolve_db_path() -> Path:
        configured = str(config.get("nodes", "db_path", default="") or "").strip()
        if configured:
            p = Path(configured)
            return p if p.is_absolute() else ROOT_DIR / p
        return DEFAULT_DB_PATH

    # ── Initialization ────────────────────────────────────────────

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS nodes (
                    node_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    node_type TEXT NOT NULL DEFAULT 'custom',
                    status TEXT NOT NULL DEFAULT 'disconnected',
                    surfaces_json TEXT DEFAULT '[]',
                    permissions_json TEXT DEFAULT '{}',
                    pairing_token TEXT,
                    pairing_expires_at TEXT,
                    connected_at TEXT,
                    last_seen_at TEXT,
                    meta_json TEXT DEFAULT '{}'
                )
            """)
            await db.commit()
        await self._load_nodes()
        self._initialized = True
        logger.info(f"NodeManager inicializado — {len(self._nodes)} nodos registrados")

    async def _load_nodes(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM nodes")
            rows = await cursor.fetchall()
        for row in rows:
            node = NodeInfo(
                node_id=row["node_id"],
                name=row["name"],
                node_type=row["node_type"],
                status=NodeStatus.DISCONNECTED.value,
                surfaces=json.loads(row["surfaces_json"] or "[]"),
                permissions=json.loads(row["permissions_json"] or "{}"),
                pairing_token=row["pairing_token"],
                pairing_expires_at=row["pairing_expires_at"],
                connected_at=row["connected_at"],
                last_seen_at=row["last_seen_at"],
                meta=json.loads(row["meta_json"] or "{}"),
            )
            self._nodes[node.node_id] = node

    # ── Pairing ───────────────────────────────────────────────────

    async def create_pairing_token(
        self,
        name: str,
        node_type: str = "custom",
        surfaces: list[str] | None = None,
        ttl_seconds: int = 300,
    ) -> dict[str, Any]:
        node_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = datetime.fromtimestamp(
            now.timestamp() + ttl_seconds, tz=timezone.utc
        )

        node = NodeInfo(
            node_id=node_id,
            name=name,
            node_type=node_type,
            status=NodeStatus.PAIRING.value,
            surfaces=surfaces or [],
            pairing_token=token,
            pairing_expires_at=expires.isoformat(),
        )

        async with self._lock:
            self._nodes[node_id] = node
            self._pairing_tokens[token] = node_id

        await self._persist_node(node)
        logger.info(f"Token de emparejamiento creado para '{name}' (tipo={node_type}, id={node_id})")
        return {
            "node_id": node_id,
            "pairing_token": token,
            "expires_at": expires.isoformat(),
        }

    async def complete_pairing(
        self,
        token: str,
        ws_sid: str,
        surfaces: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> NodeInfo | None:
        async with self._lock:
            node_id = self._pairing_tokens.pop(token, None)
            if not node_id:
                logger.warning("Token de emparejamiento inválido o expirado")
                return None

            node = self._nodes.get(node_id)
            if not node:
                return None

            if node.pairing_expires_at:
                expires = datetime.fromisoformat(node.pairing_expires_at)
                if datetime.now(timezone.utc) > expires:
                    logger.warning(f"Token expirado para nodo {node_id}")
                    del self._nodes[node_id]
                    return None

            now = datetime.now(timezone.utc).isoformat()
            node.status = NodeStatus.CONNECTED.value
            node.ws_sid = ws_sid
            node.connected_at = now
            node.last_seen_at = now
            node.pairing_token = None
            node.pairing_expires_at = None
            if surfaces:
                node.surfaces = surfaces
            if meta:
                node.meta.update(meta)

            default_perms = {s: True for s in node.surfaces}
            node.permissions = default_perms

            self._sid_to_node[ws_sid] = node_id

        await self._persist_node(node)
        await self._broadcast_node_update(node)
        logger.info(f"Nodo emparejado: {node.name} ({node.node_type}) — superficies: {node.surfaces}")
        return node

    # ── Connection Management ─────────────────────────────────────

    async def node_connected(self, node_id: str, ws_sid: str) -> NodeInfo | None:
        async with self._lock:
            node = self._nodes.get(node_id)
            if not node:
                return None
            if node.status == NodeStatus.BANNED.value:
                return None
            node.status = NodeStatus.CONNECTED.value
            node.ws_sid = ws_sid
            node.last_seen_at = datetime.now(timezone.utc).isoformat()
            self._sid_to_node[ws_sid] = node_id

        await self._persist_node(node)
        await self._broadcast_node_update(node)
        return node

    async def register_device_node(
        self,
        *,
        node_id: str,
        name: str,
        node_type: str,
        surfaces: list[str],
        ws_sid: str,
        meta: dict[str, Any] | None = None,
        sio: Any = None,
    ) -> NodeInfo:
        """Alta o reconexión de un dispositivo autenticado por token (API v1).

        No usa el emparejamiento propio del NodeManager: el token del dispositivo
        ya lo emitió local_auth. Los permisos que el usuario haya desactivado se
        conservan entre reconexiones.
        """
        await self.initialize()
        if sio is not None and self._sio is None:
            self._sio = sio
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            node = self._nodes.get(node_id)
            if node is not None and node.status == NodeStatus.BANNED.value:
                raise PermissionError("Dispositivo bloqueado")
            previous = dict(node.permissions) if node else {}
            node = NodeInfo(
                node_id=node_id,
                name=name,
                node_type=node_type,
                status=NodeStatus.CONNECTED.value,
                surfaces=list(surfaces),
                permissions={s: previous.get(s, True) for s in surfaces},
                ws_sid=ws_sid,
                connected_at=now,
                last_seen_at=now,
                meta=dict(meta or {}),
            )
            self._nodes[node_id] = node
            self._sid_to_node[ws_sid] = node_id
        await self._persist_node(node)
        await self._broadcast_node_update(node)
        logger.info(f"Dispositivo conectado como nodo: {name} — superficies: {surfaces}")
        return node

    async def node_disconnected(self, ws_sid: str) -> None:
        async with self._lock:
            node_id = self._sid_to_node.pop(ws_sid, None)
            if not node_id:
                return
            node = self._nodes.get(node_id)
            if not node:
                return
            node.status = NodeStatus.DISCONNECTED.value
            node.ws_sid = None
            node.last_seen_at = datetime.now(timezone.utc).isoformat()

        await self._persist_node(node)
        await self._broadcast_node_update(node)
        logger.info(f"Nodo desconectado: {node.name}")

    async def heartbeat(self, ws_sid: str) -> None:
        node_id = self._sid_to_node.get(ws_sid)
        if not node_id:
            return
        node = self._nodes.get(node_id)
        if node:
            node.last_seen_at = datetime.now(timezone.utc).isoformat()

    # ── Invoke Surface ────────────────────────────────────────────

    async def invoke_surface(
        self,
        node_id: str,
        surface: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        node = self._nodes.get(node_id)
        if not node:
            return {"ok": False, "error": f"Nodo {node_id} no encontrado"}
        if node.status != NodeStatus.CONNECTED.value:
            return {"ok": False, "error": f"Nodo '{node.name}' no está conectado"}
        if not node.is_surface_allowed(surface):
            return {"ok": False, "error": f"Superficie '{surface}' no permitida en nodo '{node.name}'"}
        if not self._sio or not node.ws_sid:
            return {"ok": False, "error": "Sin conexión WebSocket al nodo"}

        request_id = str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        _pending_invocations[request_id] = future

        try:
            await self._sio.emit(
                "node:invoke",
                {
                    "request_id": request_id,
                    "surface": surface,
                    "params": params or {},
                },
                to=node.ws_sid,
            )
            result = await asyncio.wait_for(future, timeout=timeout)
            return {"ok": True, "data": result}
        except asyncio.TimeoutError:
            return {"ok": False, "error": f"Timeout invocando '{surface}' en nodo '{node.name}'"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            _pending_invocations.pop(request_id, None)

    def resolve_invocation(self, request_id: str, result: dict[str, Any]) -> None:
        future = _pending_invocations.pop(request_id, None)
        if future and not future.done():
            future.set_result(result)

    # ── CRUD ──────────────────────────────────────────────────────

    async def list_nodes(self, include_disconnected: bool = True) -> list[dict[str, Any]]:
        nodes = []
        for node in self._nodes.values():
            if not include_disconnected and node.status == NodeStatus.DISCONNECTED.value:
                continue
            info = node.to_dict()
            info.pop("pairing_token", None)
            info.pop("ws_sid", None)
            nodes.append(info)
        return nodes

    async def get_node(self, node_id: str) -> NodeInfo | None:
        return self._nodes.get(node_id)

    async def remove_node(self, node_id: str) -> bool:
        async with self._lock:
            node = self._nodes.pop(node_id, None)
            if not node:
                return False
            if node.ws_sid:
                self._sid_to_node.pop(node.ws_sid, None)
                if self._sio:
                    await self._sio.emit("node:removed", {"node_id": node_id}, to=node.ws_sid)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
            await db.commit()
        await self._broadcast_node_update(node, removed=True)
        return True

    async def update_permissions(
        self, node_id: str, permissions: dict[str, bool]
    ) -> bool:
        node = self._nodes.get(node_id)
        if not node:
            return False
        node.permissions.update(permissions)
        await self._persist_node(node)
        return True

    async def ban_node(self, node_id: str) -> bool:
        node = self._nodes.get(node_id)
        if not node:
            return False
        node.status = NodeStatus.BANNED.value
        if node.ws_sid:
            async with self._lock:
                self._sid_to_node.pop(node.ws_sid, None)
            if self._sio:
                await self._sio.emit("node:banned", {"node_id": node_id}, to=node.ws_sid)
            node.ws_sid = None
        await self._persist_node(node)
        return True

    # ── Persistence ───────────────────────────────────────────────

    async def _persist_node(self, node: NodeInfo) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO nodes
                (node_id, name, node_type, status, surfaces_json, permissions_json,
                 pairing_token, pairing_expires_at, connected_at, last_seen_at, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.node_id,
                    node.name,
                    node.node_type,
                    node.status,
                    json.dumps(node.surfaces),
                    json.dumps(node.permissions),
                    node.pairing_token,
                    node.pairing_expires_at,
                    node.connected_at,
                    node.last_seen_at,
                    json.dumps(node.meta),
                ),
            )
            await db.commit()

    async def _broadcast_node_update(
        self, node: NodeInfo, *, removed: bool = False
    ) -> None:
        if not self._sio:
            return
        event = "agent:node_removed" if removed else "agent:node_update"
        info = node.to_dict()
        info.pop("pairing_token", None)
        info.pop("ws_sid", None)
        try:
            await self._sio.emit(event, info)
        except Exception:
            pass

    # ── Helpers ────────────────────────────────────────────────────

    def get_node_by_sid(self, ws_sid: str) -> NodeInfo | None:
        node_id = self._sid_to_node.get(ws_sid)
        return self._nodes.get(node_id) if node_id else None

    def connected_count(self) -> int:
        return sum(
            1 for n in self._nodes.values()
            if n.status == NodeStatus.CONNECTED.value
        )


# ── Pending Invocations (module-level for resolve_invocation) ─────

_pending_invocations: dict[str, asyncio.Future] = {}

# ── Singleton ─────────────────────────────────────────────────────

_node_manager: NodeManager | None = None


def get_node_manager() -> NodeManager:
    global _node_manager
    if _node_manager is None:
        _node_manager = NodeManager()
    return _node_manager


def set_node_manager(manager: NodeManager) -> None:
    global _node_manager
    _node_manager = manager
