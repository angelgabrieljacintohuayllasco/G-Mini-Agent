"""
G-Mini Agent — Canvas Service.
Motor de Canvas: dashboards y vistas HTML actualizables en tiempo real.
Soporta tipos: estado, dashboard, monitor, lista, tabla, custom.
"""

from __future__ import annotations

import asyncio
import html as html_mod
import json
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
DEFAULT_DB_PATH = DB_DIR / "gateway.db"


# ── Canvas Types ──────────────────────────────────────────────────

class CanvasType(str, Enum):
    STATUS = "status"
    DASHBOARD = "dashboard"
    MONITOR = "monitor"
    LIST = "list"
    TABLE = "table"
    CUSTOM = "custom"


# ── Data ──────────────────────────────────────────────────────────

@dataclass
class Canvas:
    canvas_id: str
    title: str
    canvas_type: str
    content: str  # HTML content
    data: dict[str, Any] = field(default_factory=dict)
    pinned: bool = False
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    created_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CanvasVersion:
    version_id: str
    canvas_id: str
    version: int
    content: str
    data_json: str
    saved_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Templates ─────────────────────────────────────────────────────

CANVAS_TEMPLATES: dict[str, str] = {
    "status": """<div class="canvas-status">
  <h2>{{ title }}</h2>
  <div class="status-indicator {{ status_class }}">{{ status }}</div>
  <div class="status-details">{{ details }}</div>
  <div class="status-updated">Actualizado: {{ updated_at }}</div>
</div>""",

    "dashboard": """<div class="canvas-dashboard">
  <h2>{{ title }}</h2>
  <div class="dashboard-grid">
    {% for card in cards %}
    <div class="dashboard-card">
      <div class="card-label">{{ card.label }}</div>
      <div class="card-value">{{ card.value }}</div>
      <div class="card-change {{ card.change_class }}">{{ card.change }}</div>
    </div>
    {% endfor %}
  </div>
</div>""",

    "monitor": """<div class="canvas-monitor">
  <h2>{{ title }}</h2>
  <div class="monitor-metrics">
    {% for metric in metrics %}
    <div class="monitor-row">
      <span class="metric-name">{{ metric.name }}</span>
      <div class="metric-bar">
        <div class="metric-fill" style="width:{{ metric.percent }}%"></div>
      </div>
      <span class="metric-value">{{ metric.value }}</span>
    </div>
    {% endfor %}
  </div>
</div>""",

    "list": """<div class="canvas-list">
  <h2>{{ title }}</h2>
  <ul class="canvas-items">
    {% for item in items %}
    <li class="canvas-item {{ item.class }}">
      <span class="item-icon">{{ item.icon }}</span>
      <span class="item-text">{{ item.text }}</span>
      <span class="item-meta">{{ item.meta }}</span>
    </li>
    {% endfor %}
  </ul>
</div>""",

    "table": """<div class="canvas-table">
  <h2>{{ title }}</h2>
  <table>
    <thead><tr>
      {% for col in columns %}<th>{{ col }}</th>{% endfor %}
    </tr></thead>
    <tbody>
      {% for row in rows %}
      <tr>{% for cell in row %}<td>{{ cell }}</td>{% endfor %}</tr>
      {% endfor %}
    </tbody>
  </table>
</div>""",

    "custom": """<div class="canvas-custom">{{ content }}</div>""",
}


# ── Render Helper ─────────────────────────────────────────────────

def render_template(template_str: str, data: dict[str, Any]) -> str:
    """Renderizado simple de templates con Jinja2-like syntax.
    Soporta {{ var }}, {% for %}, {% endfor %}, {% if %}, {% endif %}.
    """
    import re

    result = template_str

    # Simple variable substitution {{ var }}
    def _replace_var(m: re.Match) -> str:
        key = m.group(1).strip()
        parts = key.split(".")
        val: Any = data
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p, "")
            elif hasattr(val, p):
                val = getattr(val, p)
            else:
                val = ""
                break
        raw = str(val) if val is not None else ""
        return html_mod.escape(raw)

    # Process for loops first
    for_pattern = r'\{%\s*for\s+(\w+)\s+in\s+(\w+)\s*%\}(.*?)\{%\s*endfor\s*%\}'
    def _replace_for(m: re.Match) -> str:
        var_name = m.group(1)
        list_name = m.group(2)
        body = m.group(3)
        items = data.get(list_name, [])
        parts = []
        for item in items:
            loop_data = {**data, var_name: item}
            rendered = re.sub(r'\{\{\s*(.*?)\s*\}\}', lambda mm: _resolve_var(mm.group(1).strip(), loop_data), body)
            parts.append(rendered)
        return "".join(parts)

    result = re.sub(for_pattern, _replace_for, result, flags=re.DOTALL)

    # Process if blocks
    if_pattern = r'\{%\s*if\s+(.*?)\s*%\}(.*?)\{%\s*endif\s*%\}'
    def _replace_if(m: re.Match) -> str:
        condition_key = m.group(1).strip()
        body = m.group(2)
        val = data.get(condition_key)
        return body if val else ""

    result = re.sub(if_pattern, _replace_if, result, flags=re.DOTALL)

    # Final variable substitution
    result = re.sub(r'\{\{\s*(.*?)\s*\}\}', _replace_var, result)

    return result


def _resolve_var(key: str, data: dict[str, Any]) -> str:
    parts = key.split(".")
    val: Any = data
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p, "")
        elif hasattr(val, p):
            val = getattr(val, p)
        else:
            return ""
    raw = str(val) if val is not None else ""
    return html_mod.escape(raw)


# ── Canvas Service ────────────────────────────────────────────────

class CanvasService:
    """Gestiona el ciclo de vida de canvas y su actualización en tiempo real."""

    def __init__(self, sio: socketio.AsyncServer | None = None):
        self._sio = sio
        self._db_path = self._resolve_db_path()
        self._canvases: dict[str, Canvas] = {}
        self._subscribers: dict[str, set[str]] = {}  # canvas_id -> set of sids
        self._lock = asyncio.Lock()
        self._initialized = False

    @staticmethod
    def _resolve_db_path() -> Path:
        configured = str(config.get("canvas", "db_path", default="") or "").strip()
        if configured:
            p = Path(configured)
            return p if p.is_absolute() else ROOT_DIR / p
        return DEFAULT_DB_PATH

    # ── Init ──────────────────────────────────────────────────────

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS canvases (
                    canvas_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    canvas_type TEXT NOT NULL DEFAULT 'custom',
                    content TEXT DEFAULT '',
                    data_json TEXT DEFAULT '{}',
                    pinned INTEGER DEFAULT 0,
                    version INTEGER DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT,
                    created_by TEXT DEFAULT ''
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS canvas_versions (
                    version_id TEXT PRIMARY KEY,
                    canvas_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content TEXT DEFAULT '',
                    data_json TEXT DEFAULT '{}',
                    saved_at TEXT,
                    FOREIGN KEY (canvas_id) REFERENCES canvases(canvas_id)
                )
            """)
            await db.commit()
        await self._load_canvases()
        self._initialized = True
        logger.info(f"CanvasService inicializado — {len(self._canvases)} canvases")

    async def _load_canvases(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM canvases")
            rows = await cursor.fetchall()
        for row in rows:
            c = Canvas(
                canvas_id=row["canvas_id"],
                title=row["title"],
                canvas_type=row["canvas_type"],
                content=row["content"] or "",
                data=json.loads(row["data_json"] or "{}"),
                pinned=bool(row["pinned"]),
                version=row["version"],
                created_at=row["created_at"] or "",
                updated_at=row["updated_at"] or "",
                created_by=row["created_by"] or "",
            )
            self._canvases[c.canvas_id] = c

    # ── CRUD ──────────────────────────────────────────────────────

    async def create_canvas(
        self,
        title: str,
        canvas_type: str = "custom",
        data: dict[str, Any] | None = None,
        content: str | None = None,
        created_by: str = "agent",
    ) -> Canvas:
        now = datetime.now(timezone.utc).isoformat()
        canvas = Canvas(
            canvas_id=str(uuid.uuid4()),
            title=title,
            canvas_type=canvas_type,
            data=data or {},
            content="",
            created_at=now,
            updated_at=now,
            created_by=created_by,
        )

        if content:
            canvas.content = content
        elif canvas_type in CANVAS_TEMPLATES:
            canvas.content = render_template(CANVAS_TEMPLATES[canvas_type], canvas.data)

        async with self._lock:
            self._canvases[canvas.canvas_id] = canvas

        await self._persist_canvas(canvas)
        await self._save_version(canvas)
        await self._broadcast_canvas_event("canvas:created", canvas)
        logger.info(f"Canvas creado: '{title}' ({canvas_type})")
        return canvas

    async def update_canvas(
        self,
        canvas_id: str,
        data: dict[str, Any] | None = None,
        content: str | None = None,
        title: str | None = None,
    ) -> Canvas | None:
        async with self._lock:
            canvas = self._canvases.get(canvas_id)
            if not canvas:
                return None

            if data is not None:
                canvas.data.update(data)
            if title is not None:
                canvas.title = title
            if content is not None:
                canvas.content = content
            elif data is not None and canvas.canvas_type in CANVAS_TEMPLATES:
                canvas.content = render_template(
                    CANVAS_TEMPLATES[canvas.canvas_type], canvas.data
                )

            canvas.version += 1
            canvas.updated_at = datetime.now(timezone.utc).isoformat()

        await self._persist_canvas(canvas)
        await self._save_version(canvas)
        await self._broadcast_canvas_event("canvas:updated", canvas)
        return canvas

    async def delete_canvas(self, canvas_id: str) -> bool:
        async with self._lock:
            canvas = self._canvases.pop(canvas_id, None)
        if not canvas:
            return False
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM canvases WHERE canvas_id = ?", (canvas_id,))
            await db.execute("DELETE FROM canvas_versions WHERE canvas_id = ?", (canvas_id,))
            await db.commit()
        await self._broadcast_canvas_event("canvas:deleted", canvas)
        return True

    async def get_canvas(self, canvas_id: str) -> Canvas | None:
        return self._canvases.get(canvas_id)

    async def list_canvases(
        self, canvas_type: str | None = None, pinned_only: bool = False
    ) -> list[dict[str, Any]]:
        result = []
        for c in self._canvases.values():
            if canvas_type and c.canvas_type != canvas_type:
                continue
            if pinned_only and not c.pinned:
                continue
            result.append(c.to_dict())
        result.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return result

    async def pin_canvas(self, canvas_id: str, pinned: bool = True) -> bool:
        canvas = self._canvases.get(canvas_id)
        if not canvas:
            return False
        canvas.pinned = pinned
        await self._persist_canvas(canvas)
        return True

    # ── Version History ───────────────────────────────────────────

    async def get_versions(
        self, canvas_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT version_id, canvas_id, version, saved_at
                FROM canvas_versions
                WHERE canvas_id = ?
                ORDER BY version DESC
                LIMIT ?
                """,
                (canvas_id, limit),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_version_content(
        self, canvas_id: str, version: int
    ) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM canvas_versions WHERE canvas_id = ? AND version = ?",
                (canvas_id, version),
            )
            row = await cursor.fetchone()
        if not row:
            return None
        return {
            "version_id": row["version_id"],
            "canvas_id": row["canvas_id"],
            "version": row["version"],
            "content": row["content"],
            "data": json.loads(row["data_json"] or "{}"),
            "saved_at": row["saved_at"],
        }

    async def restore_version(self, canvas_id: str, version: int) -> Canvas | None:
        ver = await self.get_version_content(canvas_id, version)
        if not ver:
            return None
        return await self.update_canvas(
            canvas_id, data=ver.get("data"), content=ver.get("content")
        )

    # ── Subscriptions (real-time) ─────────────────────────────────

    def subscribe(self, canvas_id: str, sid: str) -> None:
        self._subscribers.setdefault(canvas_id, set()).add(sid)

    def unsubscribe(self, canvas_id: str, sid: str) -> None:
        subs = self._subscribers.get(canvas_id)
        if subs:
            subs.discard(sid)

    def unsubscribe_all(self, sid: str) -> None:
        for subs in self._subscribers.values():
            subs.discard(sid)

    # ── Persistence ───────────────────────────────────────────────

    async def _persist_canvas(self, canvas: Canvas) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO canvases
                (canvas_id, title, canvas_type, content, data_json, pinned,
                 version, created_at, updated_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    canvas.canvas_id, canvas.title, canvas.canvas_type,
                    canvas.content, json.dumps(canvas.data),
                    1 if canvas.pinned else 0, canvas.version,
                    canvas.created_at, canvas.updated_at, canvas.created_by,
                ),
            )
            await db.commit()

    async def _save_version(self, canvas: Canvas) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO canvas_versions
                (version_id, canvas_id, version, content, data_json, saved_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    canvas.canvas_id,
                    canvas.version,
                    canvas.content,
                    json.dumps(canvas.data),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()

    async def _broadcast_canvas_event(self, event: str, canvas: Canvas) -> None:
        if not self._sio:
            return

        payload = canvas.to_dict()

        # Send to subscribers
        sids = self._subscribers.get(canvas.canvas_id, set())
        for sid in list(sids):
            try:
                await self._sio.emit(event, payload, to=sid)
            except Exception:
                sids.discard(sid)

        # Also broadcast to all (for canvas list updates)
        if event in ("canvas:created", "canvas:deleted"):
            try:
                await self._sio.emit(event, payload)
            except Exception:
                pass


# ── Singleton ─────────────────────────────────────────────────────

_canvas_service: CanvasService | None = None


def get_canvas_service() -> CanvasService:
    global _canvas_service
    if _canvas_service is None:
        _canvas_service = CanvasService()
    return _canvas_service


async def ensure_canvas_service() -> CanvasService:
    """Servicio listo para usar: tablas creadas, canvases cargados y Socket.IO para avisar a la interfaz."""
    svc = get_canvas_service()
    if svc._sio is None:
        try:
            from backend.api.websocket_handler import sio

            svc._sio = sio
        except Exception as exc:  # pragma: no cover - sin servidor de sockets (tests, CLI)
            logger.debug(f"Canvas sin Socket.IO: {exc}")
    await svc.initialize()
    return svc


def set_canvas_service(service: CanvasService) -> None:
    global _canvas_service
    _canvas_service = service
