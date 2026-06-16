"""
G-Mini Agent — Memoria del agente.
Historial de conversación en memoria + persistencia en SQLite.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger

from backend.providers.base import LLMMessage

DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_DIR.mkdir(exist_ok=True)
DB_PATH = DB_DIR / "memory.db"


class Memory:
    """
    Gestiona el historial de conversación y la persistencia.
    - Historial en memoria para la sesión actual
    - SQLite para persistencia entre sesiones
    - Las sesiones solo se guardan cuando hay al menos un mensaje
    """

    def __init__(self):
        self._session_id: str = f"ses_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self._messages: list[dict[str, Any]] = []       # LLM context (user/assistant)
        self._all_messages: list[dict[str, Any]] = []   # Full display history
        self._system_prompt: str = ""
        self._session_mode: str = "normal"
        self._db_initialized = False
        self._session_registered = False  # Solo se registra al primer mensaje

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self._messages

    @property
    def all_messages(self) -> list[dict[str, Any]]:
        """All messages including display-only (system, action, etc.)."""
        return self._all_messages

    @property
    def session_mode(self) -> str:
        return self._session_mode

    async def initialize(self) -> None:
        """Inicializa la base de datos SQLite."""
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    message_count INTEGER DEFAULT 0,
                    mode TEXT DEFAULT 'normal'
                )
            """)
            try:
                await db.execute("ALTER TABLE sessions ADD COLUMN mode TEXT DEFAULT 'normal'")
            except Exception as exc:
                logger.debug(f"Columna 'mode' ya existe o migración no necesaria: {exc}")
            try:
                await db.execute("ALTER TABLE conversations ADD COLUMN message_type TEXT DEFAULT 'text'")
            except Exception:
                pass  # Ya existe
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_conv_session
                ON conversations (session_id)
            """)
            await db.commit()
            self._db_initialized = True
            logger.info(f"Memory DB inicializada: {DB_PATH}")

        # NO registramos la sesión aquí - se hace al primer mensaje

    async def _register_session(self, title: str = "") -> None:
        """Registra la sesión en la DB (solo se llama al primer mensaje)."""
        if self._session_registered:
            return
        
        now = datetime.now().isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO sessions (session_id, created_at, updated_at, title, message_count, mode) VALUES (?, ?, ?, ?, 0, ?)",
                (self._session_id, now, now, title, self._session_mode),
            )
            await db.commit()
        self._session_registered = True

    def _generate_title(self, first_message: str) -> str:
        """Genera un título corto basado en el primer mensaje del usuario."""
        # Limpiar y truncar
        title = first_message.strip()
        # Quitar saltos de línea
        title = title.replace("\n", " ").replace("\r", "")
        # Limitar a 50 caracteres
        if len(title) > 50:
            title = title[:47] + "..."
        return title if title else "Nueva conversación"

    def set_system_prompt(self, prompt: str) -> None:
        """Establece el system prompt."""
        self._system_prompt = prompt

    def set_session_mode(self, mode_key: str) -> None:
        self._session_mode = (mode_key or "normal").strip().lower() or "normal"

    async def persist_session_mode(self) -> None:
        if not self._db_initialized or not self._session_registered:
            return
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE sessions SET mode = ?, updated_at = ? WHERE session_id = ?",
                    (self._session_mode, datetime.now().isoformat(), self._session_id),
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Error al persistir modo de sesión: {e}")

    def add_user_message(self, content: str, images: list | None = None) -> None:
        """Añade un mensaje del usuario al historial. `images` opcional (base64) para mensajes multimodales."""
        entry = {
            "role": "user",
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "message_type": "text",
        }
        if images:
            entry["images"] = images
        self._messages.append(entry)
        self._all_messages.append(entry)

    def add_assistant_message(self, content: str) -> None:
        """Añade un mensaje del asistente al historial."""
        entry = {
            "role": "assistant",
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "message_type": "text",
        }
        self._messages.append(entry)
        self._all_messages.append(entry)

    def add_message_with_image(self, role: str, content: str, images: list[str]) -> None:
        """Añade un mensaje con imágenes (para re-inyección de screenshots)."""
        self._messages.append({
            "role": role,
            "content": content,
            "images": images,
            "timestamp": datetime.now().isoformat(),
        })

    def get_last_assistant_message(self) -> str | None:
        """Retorna el último mensaje del asistente."""
        for msg in reversed(self._messages):
            if msg["role"] == "assistant":
                return msg["content"]
        return None

    def get_llm_messages(self) -> list[LLMMessage]:
        """Retorna los mensajes en formato LLMMessage para enviar al provider."""
        result = []

        # System prompt primero
        if self._system_prompt:
            result.append(LLMMessage(role="system", content=self._system_prompt))

        # Historial de conversación (con imágenes si las hay)
        for msg in self._messages:
            images = msg.get("images", [])
            result.append(LLMMessage(
                role=msg["role"],
                content=msg["content"],
                images=images,
            ))

        return result

    async def persist_message(self, role: str, content: str, message_type: str = "text", metadata: dict | None = None) -> None:
        """Persiste un mensaje en SQLite.
        
        message_type: 'text' | 'tool_call' | 'tool_result'
        metadata: dict opcional con info extra (tool_name, params, success, duration_ms, etc.)
        """
        if not self._db_initialized:
            logger.warning(f"persist_message ignorado: DB no inicializada (role={role}, len={len(content)})")
            return

        try:
            # Si es el primer mensaje, registrar la sesión con título auto-generado
            if not self._session_registered:
                title = self._generate_title(content) if role == "user" else "Nueva conversación"
                await self._register_session(title)

            meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else "{}"

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO conversations (session_id, role, content, timestamp, metadata, message_type) VALUES (?, ?, ?, ?, ?, ?)",
                    (self._session_id, role, content, datetime.now().isoformat(), meta_json, message_type),
                )
                await db.execute(
                    "UPDATE sessions SET updated_at = ?, message_count = message_count + 1 WHERE session_id = ?",
                    (datetime.now().isoformat(), self._session_id),
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Error al persistir mensaje: {e}")

    async def load_session(self, session_id: str) -> None:
        """Carga una sesión anterior desde la DB."""
        self._session_id = session_id
        self._messages = []
        self._all_messages = []
        self._session_registered = False
        self._session_mode = "normal"

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT mode FROM sessions WHERE session_id = ?",
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    self._session_registered = True
                    if row[0]:
                        self._session_mode = row[0]

            async with db.execute(
                "SELECT role, content, timestamp, metadata, message_type FROM conversations WHERE session_id = ? ORDER BY id",
                (session_id,),
            ) as cursor:
                async for row in cursor:
                    meta = {}
                    if row[3]:
                        try:
                            meta = json.loads(row[3])
                        except (json.JSONDecodeError, TypeError):
                            pass
                    msg_type = row[4] if len(row) > 4 and row[4] else "text"
                    entry = {
                        "role": row[0],
                        "content": row[1],
                        "timestamp": row[2],
                        "metadata": meta,
                        "message_type": msg_type,
                    }
                    # Only add user/assistant to LLM context
                    if row[0] in ("user", "assistant"):
                        self._messages.append(entry)
                    self._all_messages.append(entry)

        logger.info(f"Sesión cargada: {session_id} ({len(self._messages)} msgs LLM, {len(self._all_messages)} total)")

    async def list_sessions(self, limit: int = 20) -> list[dict]:
        """Lista las sesiones más recientes (solo las que tienen mensajes)."""
        sessions = []
        async with aiosqlite.connect(DB_PATH) as db:
            # Solo mostrar sesiones con al menos 1 mensaje
            async with db.execute(
                "SELECT session_id, created_at, updated_at, title, message_count, mode FROM sessions WHERE message_count > 0 ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ) as cursor:
                async for row in cursor:
                    sessions.append({
                        "session_id": row[0],
                        "created_at": row[1],
                        "updated_at": row[2],
                        "title": row[3],
                        "message_count": row[4],
                        "mode": row[5] or "normal",
                    })
        return sessions

    def clear(self) -> None:
        """Limpia el historial en memoria."""
        self._messages = []
        self._all_messages = []

    @property
    def message_count(self) -> int:
        return len(self._messages)

    async def persist_display_message(
        self,
        content: str,
        message_type: str = "system",
        metadata: dict | None = None,
    ) -> None:
        """Persist a display-only message (not sent to LLM, only shown in UI).

        message_type: 'system' | 'action' | 'error' | 'screenshot'
        """
        entry = {
            "role": "display",
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "message_type": message_type,
            "metadata": metadata or {},
        }
        self._all_messages.append(entry)
        await self.persist_message(
            role="display",
            content=content,
            message_type=message_type,
            metadata=metadata,
        )
