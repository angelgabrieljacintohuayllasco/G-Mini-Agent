"""
G-Mini Agent — Agent Core.
Cerebro central que orquesta LLM, memoria, token management, vision, automation y voice.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import unicodedata
from pathlib import Path
from typing import Any

from loguru import logger

from backend.automation.editor_bridge import get_editor_bridge
from backend.config import config, ROOT_DIR
from backend.core.memory import Memory
from backend.core.ide_manager import IDEManager
from backend.core.modes import DEFAULT_MODE_KEY, build_mode_system_prompt, get_mode, get_mode_behavior_prompt, list_modes
from backend.core.policy import PolicyEngine, is_approval_text, is_rejection_text
from backend.core.prompt_manager import get_prompt_text, render_prompt_text
from backend.core.resilience import RetryPolicy
from backend.core.subagents import SubAgentOrchestrator
from backend.core.terminal_manager import TerminalManager
from backend.core.token_manager import count_messages_tokens, count_tokens, truncate_messages
from backend.core.cost_tracker import BudgetLimitExceeded, get_cost_tracker
from backend.core.avatar_context import build_avatar_context
from backend.core.emotion_tags import EmotionTagFilter, extract_emotion_tags
from backend.core.workspace_manager import WorkspaceManager
from backend.providers.base import LLMMessage
from backend.providers.router import ModelRouter
from backend.providers.base import LLMProviderUnavailableError
from backend.api.websocket_handler import (
    emit_message,
    emit_message_chunk,
    emit_message_done,
    emit_emotion,
    emit_screenshot,
    emit_media,
    emit_approval_state,
    emit_subagents_state,
    emit_status,
    sio,
)
from backend.api.schemas import AgentStatus

# Phase 2 — imports lazy para evitar bloqueos al inicio
VisionEngine = None
UIDetector = None
AutomationEngine = None
ADBController = None
BrowserController = None
ActionPlanner = None

# Phase 3 — imports lazy
VoiceEngine = None
RealTimeVoice = None
SimulatedRealtimeVoice = None


def _lazy_load_phase2():
    global VisionEngine, UIDetector, AutomationEngine, ADBController, BrowserController, ActionPlanner
    if VisionEngine is not None:
        return
    from backend.vision.engine import VisionEngine as _VE
    from backend.vision.ui_detector import UIDetector as _UID
    from backend.automation.pc_controller import AutomationEngine as _AE
    from backend.automation.adb_controller import ADBController as _ADB
    from backend.automation.browser_controller import BrowserController as _BC
    from backend.core.planner import ActionPlanner as _AP
    VisionEngine = _VE
    UIDetector = _UID
    AutomationEngine = _AE
    ADBController = _ADB
    BrowserController = _BC
    ActionPlanner = _AP


def _lazy_load_phase3():
    global VoiceEngine, RealTimeVoice, SimulatedRealtimeVoice
    if VoiceEngine is not None:
        return
    from backend.voice.engine import VoiceEngine as _Voice
    from backend.voice.realtime import RealTimeVoice as _RTV
    from backend.voice.simulated_realtime import SimulatedRealtimeVoice as _SRV
    VoiceEngine = _Voice
    RealTimeVoice = _RTV
    SimulatedRealtimeVoice = _SRV


# System prompt por defecto (se carga desde archivo externo configurable)
def _load_system_prompt() -> str:
    """Carga el system prompt base desde config o archivo por defecto."""
    text, source = get_prompt_text("system_base", fallback=_FALLBACK_SYSTEM_PROMPT)
    logger.info(f"System prompt base cargado desde: {source}")
    return text

_FALLBACK_SYSTEM_PROMPT = """# G-MINI AGENT — AGENTE AUTÓNOMO DE CONTROL DE PC
Eres G-Mini Agent, un agente IA que controla la computadora del usuario.
Usa [ACTION:...] para ejecutar acciones. Responde en español."""

EMOTION_TAGS_PROMPT = """[EXPRESIONES DEL AVATAR]
Tu avatar puede mostrar emociones faciales y corporales. Cuando sea natural,
antepone a tu respuesta (o a la oración relevante) UNO de estos tags para
reflejar tu estado de animo: [happy] [sad] [angry] [surprised] [relaxed] [neutral].
No abuses de ellos (maximo 1-2 por respuesta), no los expliques ni los
menciones, y no los uses si no aportan nada."""

DEFAULT_DELEGATION_PLANNER_PROMPT = """Eres el orquestador de sub-agentes de G-Mini Agent.
Decide si una solicitud analitica conviene dividirla en 2 o 3 subtareas paralelas.
Responde SOLO JSON valido con este formato:
{{"group_name":"...","subtasks":[{{"title":"...","task":"...","mode":"investigador"}}]}}
Usa modos existentes: {available_modes}.
Si no conviene delegar, devuelve {{"group_name":"","subtasks":[]}}."""

DEFAULT_SYSTEM_PROMPT = _load_system_prompt()


def _normalize_match_text(value: Any) -> str:
    raw = str(value or "")
    normalized = unicodedata.normalize("NFKD", raw)
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _extract_data_url_base64(value: str) -> str:
    raw = str(value or "").strip()
    if raw.startswith("data:") and "," in raw:
        _, _, tail = raw.partition(",")
        return tail.strip()
    return raw


_OCR_TRUNCATE_MAX = 1500
_OCR_HEAD_SIZE = 600
_OCR_TAIL_SIZE = 300
_OCR_UI_KEYWORDS = frozenset({
    "button", "submit", "search", "login", "sign", "error", "warning",
    "cancel", "accept", "ok", "save", "delete", "close", "next", "back",
    "continue", "password", "email", "username", "click", "open", "download",
    "buscar", "iniciar", "guardar", "cancelar", "aceptar", "cerrar",
    "enviar", "eliminar", "continuar", "siguiente", "anterior", "error",
    "contraseña", "correo", "usuario", "descargar", "abrir",
})


def _smart_truncate_ocr(text: str, max_chars: int = _OCR_TRUNCATE_MAX) -> str:
    """Trunca texto OCR preservando inicio, fin y líneas con keywords de UI."""
    if len(text) <= max_chars:
        return text

    lines = text.splitlines()
    head_text = text[:_OCR_HEAD_SIZE]
    tail_text = text[-_OCR_TAIL_SIZE:]

    # Encontrar líneas del medio que contienen keywords de UI
    head_end = len(head_text)
    tail_start = len(text) - _OCR_TAIL_SIZE
    middle_text = text[head_end:tail_start]
    middle_ui_lines: list[str] = []
    budget = max_chars - _OCR_HEAD_SIZE - _OCR_TAIL_SIZE - 60  # 60 chars para separadores

    if middle_text and budget > 0:
        for line in middle_text.splitlines():
            stripped = line.strip().lower()
            if not stripped:
                continue
            words = set(re.findall(r"[a-záéíóúñü]+", stripped))
            if words & _OCR_UI_KEYWORDS:
                if budget >= len(line):
                    middle_ui_lines.append(line)
                    budget -= len(line) + 1  # +1 for newline

    omitted = len(text) - _OCR_HEAD_SIZE - _OCR_TAIL_SIZE
    separator = f"\n[... {omitted} caracteres omitidos ...]\n"
    if middle_ui_lines:
        return head_text + separator + "\n".join(middle_ui_lines) + separator + tail_text
    return head_text + separator + tail_text


def _truncate_feedback_value(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _append_confirmed_value(
    entries: list[dict[str, Any]],
    seen: set[tuple[str, str]],
    *,
    key: str,
    value: Any,
    source: str,
    confidence: float | None = None,
) -> None:
    normalized_key = str(key or "").strip().lower()
    cleaned_value = _truncate_feedback_value(value)
    if not normalized_key or not cleaned_value:
        return
    dedupe_key = (normalized_key, _normalize_match_text(cleaned_value).strip())
    if dedupe_key in seen:
        return
    seen.add(dedupe_key)
    entry: dict[str, Any] = {
        "key": normalized_key,
        "value": cleaned_value,
        "source": str(source or "").strip() or "unknown",
    }
    if confidence is not None:
        try:
            entry["confidence"] = round(float(confidence), 3)
        except (TypeError, ValueError):
            pass
    entries.append(entry)


def _collect_confirmed_action_values(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for result in results:
        if not result.get("success"):
            continue

        action_name = str(result.get("action", "") or "").strip()
        data = result.get("data") or {}
        if not isinstance(data, dict):
            continue

        resolved_answer = str(
            data.get("resolved_answer_text")
            or data.get("answer_text")
            or ""
        ).strip()
        if resolved_answer:
            confidence = data.get("confidence")
            resolution = data.get("resolution") or {}
            if confidence is None and isinstance(resolution, dict):
                confidence = resolution.get("confidence")
            _append_confirmed_value(
                entries,
                seen,
                key="resultado_confirmado",
                value=resolved_answer,
                source=action_name,
                confidence=confidence,
            )

        if action_name in {"screen_read_text", "adb_screen_read_text"}:
            extraction = data.get("extraction") or {}
            if isinstance(extraction, dict) and extraction.get("can_answer"):
                answer_text = str(extraction.get("answer_text", "") or "").strip()
                if answer_text:
                    _append_confirmed_value(
                        entries,
                        seen,
                        key="resultado_confirmado",
                        value=answer_text,
                        source=action_name,
                        confidence=extraction.get("confidence"),
                    )

        if action_name.endswith("_fallback_resolution"):
            answer_text = str(data.get("answer_text", "") or "").strip()
            if answer_text:
                _append_confirmed_value(
                    entries,
                    seen,
                    key="resultado_confirmado",
                    value=answer_text,
                    source=action_name,
                    confidence=data.get("confidence"),
                )

        if action_name == "browser_page_info":
            title_text = str(data.get("title") or data.get("title_guess") or "").strip()
            if title_text:
                _append_confirmed_value(
                    entries,
                    seen,
                    key="titulo_confirmado",
                    value=title_text,
                    source=action_name,
                )

        if action_name == "file_read_text":
            content_text = str(data.get("content", "") or "").strip()
            if content_text:
                _append_confirmed_value(
                    entries,
                    seen,
                    key="contenido_confirmado",
                    value=content_text,
                    source=action_name,
                )

        if action_name == "file_search_text":
            matches = data.get("matches") or []
            if isinstance(matches, list) and len(matches) == 1:
                first_match = matches[0] or {}
                if isinstance(first_match, dict):
                    line_text = str(first_match.get("line_text", "") or "").strip()
                    if line_text:
                        _append_confirmed_value(
                            entries,
                            seen,
                            key="coincidencia_confirmada",
                            value=line_text,
                            source=action_name,
                        )

        if action_name == "file_read_batch":
            files = data.get("files") or []
            if isinstance(files, list) and len(files) == 1:
                first_file = files[0] or {}
                if isinstance(first_file, dict):
                    content_text = str(first_file.get("content", "") or "").strip()
                    if content_text:
                        _append_confirmed_value(
                            entries,
                            seen,
                            key="contenido_confirmado",
                            value=content_text,
                            source=action_name,
                        )
                    raw_path = str(first_file.get("path", "") or "").strip()
                    if raw_path:
                        _append_confirmed_value(
                            entries,
                            seen,
                            key="ruta_confirmada",
                            value=raw_path,
                            source=action_name,
                        )
                        file_name = Path(raw_path).name.strip()
                        if file_name:
                            _append_confirmed_value(
                                entries,
                                seen,
                                key="archivo_confirmado",
                                value=file_name,
                                source=action_name,
                            )

        if action_name == "file_list":
            entries_list = data.get("entries") or []
            if isinstance(entries_list, list) and len(entries_list) == 1:
                first_entry = entries_list[0] or {}
                if isinstance(first_entry, dict):
                    raw_path = str(first_entry.get("path", "") or "").strip()
                    if raw_path:
                        _append_confirmed_value(
                            entries,
                            seen,
                            key="ruta_confirmada",
                            value=raw_path,
                            source=action_name,
                        )
                        file_name = str(first_entry.get("name", "") or Path(raw_path).name).strip()
                        if file_name:
                            _append_confirmed_value(
                                entries,
                                seen,
                                key="archivo_confirmado",
                                value=file_name,
                                source=action_name,
                            )

        if action_name in {"file_write_text", "file_exists", "file_replace_text"}:
            raw_path = str(data.get("path", "") or result.get("message", "") or "").strip()
            if raw_path:
                _append_confirmed_value(
                    entries,
                    seen,
                    key="ruta_confirmada",
                    value=raw_path,
                    source=action_name,
                )
                file_name = Path(raw_path).name.strip()
                if file_name:
                    _append_confirmed_value(
                        entries,
                        seen,
                        key="archivo_confirmado",
                        value=file_name,
                        source=action_name,
                    )

    return entries


class LLMResponseUnavailableError(RuntimeError):
    """El proveedor LLM no devolvió una respuesta utilizable tras los reintentos."""


class AgentCore:
    """
    Núcleo del agente.
    Orquesta: memoria, LLM, vision, automation, voice y streaming al frontend.
    """

    def __init__(self):
        self._router: ModelRouter | None = None
        self._memory: Memory = Memory()
        self._running = False
        self._paused = False
        self._cancel_event: asyncio.Event = asyncio.Event()
        self._pause_event: asyncio.Event = asyncio.Event()
        self._pause_event.set()  # Starts unpaused
        self._current_task: asyncio.Task | None = None
        self._active_sid: str = ""
        self._last_status: AgentStatus = AgentStatus.IDLE
        self._status_before_pause: AgentStatus = AgentStatus.IDLE
        self._session_context_lock: asyncio.Lock = asyncio.Lock()
        self._base_system_prompt: str = DEFAULT_SYSTEM_PROMPT
        self._current_mode: str = DEFAULT_MODE_KEY
        self._policy = PolicyEngine()
        self._pending_approval: dict[str, Any] | None = None
        self._subagents = SubAgentOrchestrator(max_active=5)
        self._subagent_groups_summarized: set[str] = set()
        self._terminals = TerminalManager(
            max_sessions=int(config.get("terminals", "max_sessions", default=10))
        )
        self._workspace = WorkspaceManager()
        self._ide = IDEManager(self._workspace)
        self._editor_bridge = get_editor_bridge()
        self._cost_tracker = get_cost_tracker()

        # Phase 2 — lazy init con protección
        self._vision = None
        self._ui_detector = None
        self._automation = None
        self._adb = None
        self._browser = None
        self._planner = None
        self._computer_use_agent = None
        self._phase2_available = False

        try:
            _lazy_load_phase2()
            self._vision = VisionEngine()
            self._ui_detector = UIDetector()
            self._automation = AutomationEngine()
            self._adb = ADBController()
            self._browser = BrowserController()
            self._phase2_available = True
        except ImportError as ie:
            logger.error(f"Phase 2 no disponible — dependencia faltante: {ie}")
        except Exception as e:
            logger.warning(f"Phase 2 modules parcialmente disponibles: {e}")
            # Si al menos tenemos los módulos core, marcamos como parcialmente disponible
            if self._vision and self._automation:
                self._phase2_available = True

        # Phase 3 — lazy init con protección
        self._voice = None
        self._realtime_voice = None
        self._simulated_realtime = None
        self._phase3_available = False

        try:
            _lazy_load_phase3()
            self._voice = VoiceEngine()
            self._realtime_voice = RealTimeVoice()
            self._simulated_realtime = SimulatedRealtimeVoice()
            self._phase3_available = True
        except ImportError as ie:
            logger.error(f"Phase 3 no disponible — dependencia faltante: {ie}")
        except Exception as e:
            logger.warning(f"Phase 3 modules parcialmente disponibles: {e}")
            if self._voice:
                self._phase3_available = True

    async def _set_agent_status(self, sid: str, status: AgentStatus) -> None:
        target_sid = str(sid or self._active_sid or "").strip()
        if not target_sid:
            return
        self._last_status = status
        await emit_status(target_sid, status)

    async def _emit_activity(self, sid: str, text: str, msg_type: str = "system", *, done: bool = True) -> None:
        """Emit a message to the frontend AND persist it in display history."""
        await emit_message(sid, text, msg_type, done=done)
        await self._memory.persist_display_message(text, message_type=msg_type)

    @property
    def memory(self) -> Memory:
        return self._memory

    async def _handle_pending_approval(self, sid: str, text: str) -> bool:
        if not self._pending_approval:
            return False

        pending_sid = str(self._pending_approval.get("sid") or "").strip()
        if pending_sid and pending_sid != sid:
            await emit_message(
                sid,
                "Hay acciones sensibles pendientes en otra sesion. Resuelvelas primero antes de continuar aqui.",
                "warning",
                done=True,
            )
            return True

        if is_approval_text(text):
            await self.approve_pending(sid)
            return True

        if is_rejection_text(text):
            await self.reject_pending(sid)
            return True

        await emit_message(
            sid,
            "Hay acciones sensibles pendientes. Responde 'aprobar' para ejecutarlas o 'cancelar' para descartarlas.",
            "warning",
            done=True,
        )
        return True

    def get_pending_approval_token(self, sid: str) -> str:
        if not self._pending_approval:
            return ""
        pending_sid = str(self._pending_approval.get("sid") or "").strip()
        if pending_sid and pending_sid != sid:
            return ""
        return str(self._pending_approval.get("token") or "").strip()

    async def approve_pending(self, sid: str, token: str | None = None) -> bool:
        if not self._pending_approval:
            await emit_message(sid, "No hay acciones pendientes por aprobar.", "system", done=True)
            return False
        pending_sid = str(self._pending_approval.get("sid") or "").strip()
        if pending_sid and pending_sid != sid:
            await emit_message(
                sid,
                "La aprobacion pendiente pertenece a otra sesion y no puede ejecutarse desde aqui.",
                "warning",
                done=True,
            )
            return False
        expected_token = str(self._pending_approval.get("token") or "").strip()
        supplied_token = str(token or "").strip()
        if expected_token and supplied_token and supplied_token != expected_token:
            await emit_message(
                sid,
                "Esta aprobacion ya no es valida o pertenece a otra solicitud. Revisa el ultimo mensaje pendiente.",
                "warning",
                done=True,
            )
            return False
        await emit_message(sid, "Aprobacion recibida. Ejecutando acciones pendientes.", "system", done=False)
        await self._execute_approved_actions(sid)
        return True

    async def reject_pending(self, sid: str, token: str | None = None) -> bool:
        if not self._pending_approval:
            await emit_message(sid, "No hay acciones pendientes por cancelar.", "system", done=True)
            return False
        pending_sid = str(self._pending_approval.get("sid") or "").strip()
        if pending_sid and pending_sid != sid:
            await emit_message(
                sid,
                "La aprobacion pendiente pertenece a otra sesion y no puede cancelarse desde aqui.",
                "warning",
                done=True,
            )
            return False
        expected_token = str(self._pending_approval.get("token") or "").strip()
        supplied_token = str(token or "").strip()
        if expected_token and supplied_token and supplied_token != expected_token:
            await emit_message(
                sid,
                "Esta cancelacion ya no es valida o pertenece a otra solicitud. Revisa el ultimo mensaje pendiente.",
                "warning",
                done=True,
            )
            return False
        self._pending_approval = None
        await emit_approval_state(sid, pending=False)
        await self._set_agent_status(sid, AgentStatus.IDLE)
        await emit_message(sid, "Acciones sensibles canceladas por el usuario.", "warning", done=True)
        return True

    def _build_gateway_session_id(self, channel: str, session_key: str) -> str:
        raw = f"{str(channel or '').strip().lower()}::{str(session_key or '').strip().lower()}"
        slug = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")[:48] or "session"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        return f"gw_{slug}_{digest}"

    async def process_gateway_message(
        self,
        channel: str,
        session_key: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        from backend.core.gateway_service import get_gateway

        target_session_id = self._build_gateway_session_id(channel, session_key)
        sid = get_gateway().build_virtual_sid(channel, session_key)

        async with self._session_context_lock:
            previous_session_id = self._memory.session_id
            previous_mode = self._current_mode
            try:
                if previous_session_id != target_session_id:
                    await self.load_session(target_session_id)
                await self._process_message_core(sid, text, attachments)
            finally:
                if previous_session_id != target_session_id:
                    try:
                        await self.load_session(previous_session_id)
                        if self._current_mode != previous_mode:
                            self._current_mode = previous_mode
                            self._memory.set_session_mode(previous_mode)
                            self._apply_system_prompt()
                    except Exception as exc:
                        logger.warning(
                            f"No se pudo restaurar la sesion previa {previous_session_id}: {exc}"
                        )

    async def set_gateway_session_mode(
        self,
        channel: str,
        session_key: str,
        mode_key: str,
    ) -> dict[str, Any]:
        target_session_id = self._build_gateway_session_id(channel, session_key)
        async with self._session_context_lock:
            previous_session_id = self._memory.session_id
            previous_mode = self._current_mode
            try:
                if previous_session_id != target_session_id:
                    await self.load_session(target_session_id)
                mode = get_mode(mode_key)
                self._current_mode = mode.key
                self._memory.set_session_mode(mode.key)
                self._apply_system_prompt()
                await self._memory.persist_session_mode()
                return {
                    "current_mode": mode.key,
                    "current_mode_name": mode.name,
                    "description": mode.description,
                    "behavior_prompt": get_mode_behavior_prompt(mode.key),
                    "system_prompt": mode.system_prompt,
                    "is_custom": mode.is_custom,
                    "allowed_capabilities": list(mode.allowed_capabilities),
                    "restricted_capabilities": list(mode.restricted_capabilities),
                    "requires_scope_confirmation": mode.requires_scope_confirmation,
                }
            finally:
                if previous_session_id != target_session_id:
                    try:
                        await self.load_session(previous_session_id)
                        if self._current_mode != previous_mode:
                            self._current_mode = previous_mode
                            self._memory.set_session_mode(previous_mode)
                            self._apply_system_prompt()
                    except Exception as exc:
                        logger.warning(
                            f"No se pudo restaurar la sesion previa {previous_session_id}: {exc}"
                        )

    async def _emit_subagents_snapshot(self, sid: str, last_event: dict[str, Any] | None = None) -> None:
        await emit_subagents_state(sid, self._subagents.list_agents(), last_event=last_event)

    def _normalize_attachments(self, attachments: list[Any] | None) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in attachments or []:
            if isinstance(item, dict):
                normalized.append({str(key): value for key, value in item.items()})
                continue
            if not isinstance(item, str):
                continue
            text_value = item.strip()
            if not text_value:
                continue
            candidate_path = Path(text_value)
            if candidate_path.exists():
                normalized.append(
                    {
                        "kind": "file",
                        "file_name": candidate_path.name,
                        "local_path": str(candidate_path),
                    }
                )
                continue
            normalized.append(
                {
                    "kind": "image",
                    "file_name": "attachment.png",
                    "image_base64": _extract_data_url_base64(text_value),
                }
            )
        return normalized

    def _build_attachment_context(
        self,
        attachments: list[dict[str, Any]] | None,
    ) -> tuple[str, list[str]]:
        items = self._normalize_attachments(attachments)
        if not items:
            return "", []

        context_lines = [
            "[SISTEMA: El usuario incluyo adjuntos. Usa este material como contexto antes de responder o actuar.]",
        ]
        inline_images: list[str] = []

        for index, item in enumerate(items, start=1):
            kind = str(item.get("kind") or item.get("type") or "file").strip().lower() or "file"
            file_name = str(
                item.get("file_name")
                or item.get("name")
                or item.get("filename")
                or f"adjunto_{index}"
            ).strip() or f"adjunto_{index}"
            mime_type = str(item.get("mime_type") or item.get("content_type") or "").strip()
            caption = str(item.get("caption") or "").strip()
            local_path = str(item.get("local_path") or item.get("path") or "").strip()
            image_base64 = _extract_data_url_base64(
                str(item.get("image_base64") or item.get("base64") or "").strip()
            )

            details = [f"{index}. {file_name}", f"tipo={kind}"]
            if mime_type:
                details.append(f"mime={mime_type}")
            size_bytes = item.get("size_bytes")
            if isinstance(size_bytes, int) and size_bytes > 0:
                details.append(f"bytes={size_bytes}")
            if local_path:
                details.append(f"path={local_path}")
            if caption:
                details.append(f"nota={caption}")
            if image_base64:
                details.append("imagen_incluida_en_contexto=true")
                inline_images.append(image_base64)
            context_lines.append("- " + " | ".join(details))

        return "\n".join(context_lines), inline_images

    async def _handle_subagent_request(self, sid: str, text: str) -> bool:
        if not self._router:
            return False

        text_lower = text.lower()
        if "subagente" not in text_lower and "subagentes" not in text_lower and "deleg" not in text_lower:
            return False

        if any(phrase in text_lower for phrase in ["estado subagentes", "listar subagentes", "lista subagentes", "ver subagentes"]):
            await self._emit_subagents_snapshot(sid)
            items = self._subagents.list_agents()
            if not items:
                await emit_message(sid, "No hay sub-agentes registrados todavia.", "system", done=True)
            else:
                lines = [f"- {item['name']} ({item['status']}): {item['task']}" for item in items[:5]]
                await emit_message(sid, "Estado de sub-agentes:\n" + "\n".join(lines), "text", done=True)
            return True

        recent_context = "\n".join(
            f"{msg['role']}: {msg['content'][:240]}"
            for msg in self._memory.messages[-4:]
        )
        try:
            spawned = await self._subagents.spawn(
                router=self._router,
                task=text,
                mode_key=self._current_mode,
                parent_mode_key=self._current_mode,
                session_id=self._memory.session_id,
                parent_task_limit_usd=self._get_current_task_budget_limit(),
                context_excerpt=recent_context,
                on_update=lambda event: self._on_subagent_update(sid, event),
                planner=self._planner,
            )
        except Exception as exc:
            await emit_message(sid, f"No pude lanzar el sub-agente: {exc}", "error", done=True)
            return True

        await emit_message(
            sid,
            f"Sub-agente lanzado: {spawned['name']} ({spawned['id']}). Te avisare cuando termine.",
            "system",
            done=True,
        )
        return True

    async def _maybe_auto_delegate(self, sid: str, text: str) -> bool:
        if not self._router:
            return False

        text_lower = text.lower()
        analytical_keywords = [
            "investiga", "analiza", "compara", "resume", "plan", "estrategia",
            "propuesta", "ideas", "perfil", "benchmark", "roadmap",
        ]
        programming_keywords = [
            "programa", "crea un", "desarrolla", "codifica", "implementa",
            "refactoriza", "escribe un script", "haz un programa", "crea una app",
            "build", "code", "develop", "create a", "write a script",
            "genera código", "genera codigo", "haz un bot", "crea un juego",
        ]
        split_markers = [" y ", " luego ", " ademas ", " además ", ",", ";"]

        is_programming = any(kw in text_lower for kw in programming_keywords)
        is_analytical = any(kw in text_lower for kw in analytical_keywords)

        if not is_programming and not is_analytical:
            return False
        if len(text) < 60 if is_programming else len(text) < 90:
            return False
        if is_analytical and not any(marker in text_lower for marker in split_markers):
            return False

        plan = await self._build_delegation_plan(text)
        subtasks = plan.get("subtasks", [])
        if len(subtasks) < 1:
            return False

        # For programming tasks, enrich subtasks with model assignment and execution capability
        if is_programming:
            for subtask in subtasks:
                task_type = self._classify_subtask_type(subtask.get("task", ""))
                model_override, provider_override = self._resolve_model_assignment(task_type)
                subtask["model_override"] = model_override
                subtask["provider_override"] = provider_override
                subtask["can_execute"] = True
                subtask["max_iterations"] = 15

        recent_context = "\n".join(
            f"{msg['role']}: {msg['content'][:240]}"
            for msg in self._memory.messages[-4:]
        )
        batch = await self._subagents.spawn_batch(
            router=self._router,
            subtasks=subtasks[:6],
            default_mode_key=self._current_mode,
            parent_mode_key=self._current_mode,
            session_id=self._memory.session_id,
            parent_task_limit_usd=self._get_current_task_budget_limit(),
            context_excerpt=recent_context,
            group_name=plan.get("group_name") or "Delegacion automatica",
            on_update=lambda event: self._on_subagent_update(sid, event),
            planner=self._planner,
        )
        names = ", ".join(item["name"] for item in batch["items"])
        executor_label = " (con ejecución)" if is_programming else ""
        await self._emit_activity(
            sid,
            f"Delegue la tarea en {len(batch['items'])} sub-agentes{executor_label}: {names}. "
            "Te compartire el consolidado cuando terminen.",
            "system",
        )
        return True

    def _classify_subtask_type(self, task_text: str) -> str:
        """Classify a subtask to resolve optimal model assignment."""
        text = task_text.lower()
        if any(kw in text for kw in ["frontend", "html", "css", "react", "vue", "ui", "interfaz"]):
            return "frontend"
        if any(kw in text for kw in ["backend", "api", "servidor", "base de datos", "sql", "python", "código", "codigo", "programa", "script", "función", "funcion", "clase", "implementa"]):
            return "programacion"
        if any(kw in text for kw in ["lee", "leer", "resume", "resumen", "analiza el código", "revisa"]):
            return "lectura"
        if any(kw in text for kw in ["diseño", "creativo", "logo", "ilustra", "historia", "narrativa"]):
            return "creatividad"
        if any(kw in text for kw in ["calcula", "estadística", "formula", "ecuación", "matemática"]):
            return "matematicas"
        return "programacion"

    def _resolve_model_assignment(self, task_type: str) -> tuple[str | None, str | None]:
        """Resolve model+provider for a task type from config, checking API key availability."""
        assignment = str(config.get("model_assignments", task_type, default="") or "").strip()
        if not assignment or ":" not in assignment:
            return None, None
        provider_name, model_name = assignment.split(":", 1)
        # Check if provider has an API key configured (is usable)
        if self._router and provider_name in self._router._providers:
            return model_name.strip(), provider_name.strip()
        # Provider not available, fall back to default
        return None, None

    async def _build_delegation_plan(self, text: str) -> dict[str, Any]:
        if not self._router:
            return {"subtasks": []}

        prompt = render_prompt_text(
            "delegation_planner",
            fallback=DEFAULT_DELEGATION_PLANNER_PROMPT,
            variables={
                "available_modes": ", ".join(mode["key"] for mode in list_modes()),
            },
        )
        response = await self._router.generate_complete_cost_aware(
            messages=[
                LLMMessage(role="system", content=prompt),
                LLMMessage(role="user", content=text),
            ],
            model=self._router.get_current_model(),
            provider_name=self._router.get_current_provider_name(),
            session_id=self._memory.session_id,
            mode_key=self._current_mode,
            source="delegation_planner",
            temperature=0.2,
            max_tokens=700,
        )
        usage_event = await self._record_llm_usage(
            provider=response.provider or self._router.get_current_provider_name(),
            model=response.model or self._router.get_current_model(),
            source="delegation_planner",
            input_tokens=int(response.input_tokens or 0),
            output_tokens=int(response.output_tokens or 0),
            estimated=False,
            mode_key=self._current_mode,
            worker_id="planner",
            worker_kind="planner",
            metadata={"max_tokens": 700},
        )
        await self._raise_if_budget_exceeded(usage_event)
        return self._parse_delegation_json(response.text)

    def _parse_delegation_json(self, raw_text: str) -> dict[str, Any]:
        text = raw_text.strip()
        code_block = re.search(r"```json\s*([\s\S]*?)```", text, re.IGNORECASE)
        if code_block:
            text = code_block.group(1).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"group_name": "", "subtasks": []}

        subtasks = []
        for item in data.get("subtasks", []):
            if not isinstance(item, dict):
                continue
            task = str(item.get("task", "")).strip()
            if not task:
                continue
            subtasks.append(
                {
                    "title": str(item.get("title", "Subtarea")).strip() or "Subtarea",
                    "task": task,
                    "mode": str(item.get("mode", self._current_mode)).strip().lower() or self._current_mode,
                }
            )

        return {
            "group_name": str(data.get("group_name", "")).strip(),
            "subtasks": subtasks,
        }

    async def _on_subagent_update(self, sid: str, event: dict[str, Any]) -> None:
        await self._emit_subagents_snapshot(sid, last_event=event)
        status = event.get("status")
        name = event.get("name", "Sub-agente")

        if status == "completed":
            preview = event.get("result_preview") or "Sin resumen disponible."
            await self._emit_activity(sid, f"Resultado de {name}: {preview}", "text")
        elif status == "failed":
            await self._emit_activity(sid, f"{name} fallo: {event.get('error', 'sin detalle')}", "error")

        group_id = event.get("group_id")
        if group_id and status in {"completed", "failed"} and self._subagents.is_group_finished(group_id):
            if group_id not in self._subagent_groups_summarized:
                self._subagent_groups_summarized.add(group_id)
                await self._emit_subagent_group_summary(sid, group_id)

    async def _emit_subagent_group_summary(self, sid: str, group_id: str) -> None:
        items = self._subagents.get_group_items(group_id)
        if not items:
            return
        group_name = items[0].get("group_name") or "Delegacion automatica"
        lines = []
        for item in items:
            preview = item.get("result_preview") or item.get("error") or "Sin resultado."
            lines.append(f"- {item['name']} ({item['status']}): {preview}")
        await self._emit_activity(
            sid,
            f"Consolidado del lote {group_name}:\n" + "\n".join(lines),
            "text",
        )

    async def run_crew_for_user(self, sid: str, crew_id: str, tasks: list[dict[str, str]]) -> None:
        """Run a crew triggered from chat and stream results via WebSocket."""
        from backend.api.websocket_handler import sio
        from backend.core.crew_engine import get_crew_engine

        engine = get_crew_engine()
        defn = engine.get_crew(crew_id)
        if defn is None:
            await emit_message(sid, f"Crew no encontrada: {crew_id}", "error", done=True)
            return

        await emit_message(
            sid,
            f"Iniciando crew **{defn.name}** ({defn.process}) con {len(tasks)} tarea(s)...",
            "system",
            done=True,
        )

        async def on_update(event):
            await sio.emit("crew:update", event, to=sid)

        run = await engine.run_crew(
            crew_id=crew_id,
            tasks=tasks,
            router=self._router,
            subagent_orchestrator=self._subagents,
            session_id=self._memory.session_id,
            parent_mode_key=self._current_mode,
            parent_task_limit_usd=self._get_current_task_budget_limit(),
            on_update=on_update,
            planner=self._planner,
        )

        if run.status == "completed":
            await emit_message(sid, f"Crew **{defn.name}** completada.\n\n{run.final_output}", "text", done=True)
        else:
            await emit_message(sid, f"Crew **{defn.name}** falló: {run.error}", "error", done=True)

        await sio.emit("crew:finished", run.to_dict(), to=sid)

    def _build_critic_context_excerpt(self, actions: list[Any]) -> str:
        recent_context = "\n".join(
            f"{msg['role']}: {msg['content'][:240]}"
            for msg in self._memory.messages[-4:]
        )
        action_lines = [
            f"- {action.type}: {json.dumps(action.params, ensure_ascii=False)}"
            for action in actions
        ]
        action_summary = "\n".join(action_lines) or "- sin acciones"
        return (
            "Contexto conversacional reciente:\n"
            f"{recent_context or 'Sin contexto reciente.'}\n\n"
            "Plan de acciones candidato:\n"
            f"{action_summary}"
        )

    def _format_action_preview(self, action: Any) -> str:
        params = getattr(action, "params", {}) or {}
        try:
            params_json = json.dumps(params, ensure_ascii=False)
        except TypeError:
            params_json = str(params)
        if len(params_json) > 220:
            params_json = params_json[:217] + "..."
        return f"- {getattr(action, 'type', 'accion')}: {params_json}"

    def _build_dry_run_preview(self, actions: list[Any], review: dict[str, Any]) -> str:
        action_lines = "\n".join(
            self._format_action_preview(action)
            for action in actions
        ) or "- Sin acciones propuestas."
        findings_lines = "\n".join(
            f"- {item.get('action', '?')} ({item.get('severity', '?')}, "
            f"score {item.get('confidence', 0.0):.2f}/{item.get('threshold', 0.0):.2f}): "
            f"{item.get('reason', 'sin detalle')}"
            for item in review.get("findings", [])
        ) or "- Sin hallazgos adicionales."
        critic = review.get("critic") or {}
        return render_prompt_text(
            "dry_run_preview",
            fallback=(
                "DRY RUN AUTOMATICO - simulacion sin ejecutar.\n\n"
                "Modo activo: {mode_name}\n"
                "Decision del critic: {critic_decision} (confianza {critic_confidence:.2f}, umbral {highest_threshold:.2f})\n"
                "Resumen del critic: {critic_summary}\n\n"
                "Plan simulado:\n{action_lines}\n\n"
                "Hallazgos y riesgos:\n{findings_lines}\n\n"
                "Revisa esta simulacion. Si se ve correcta, aprueba para ejecutar de verdad."
            ),
            variables={
                "mode_name": review.get("mode_name", "modo activo"),
                "critic_decision": critic.get("decision", "approve"),
                "critic_confidence": float(critic.get("confidence", 0.0)),
                "highest_threshold": float(review.get("highest_threshold", 0.0)),
                "critic_summary": critic.get("summary", "Sin resumen del critic."),
                "action_lines": action_lines,
                "findings_lines": findings_lines,
            },
        )

    async def _handle_computer_use_delegation(
        self, sid: str, task: str, target_monitor: int = 0
    ):
        """Delega una tarea de interacción UI al sub-agente de computer use."""
        if not self._computer_use_agent:
            logger.warning("Computer Use sub-agent no disponible para delegación")
            await emit_message(
                sid,
                "Sub-agente de computer use no disponible. "
                "Verifica que la API key de Google esté configurada y computer_use.enabled = true.",
                "warning",
                done=False,
            )
            return None

        logger.info(f"Delegando a computer use: {task[:100]} | monitor={target_monitor}")
        await emit_message(
            sid,
            f"Delegando tarea al sub-agente de computer use: {task[:150]}",
            "info",
            done=False,
        )
        await self._set_agent_status(sid, AgentStatus.EXECUTING)

        async def _on_progress(data: dict):
            iteration = data.get("iteration", 0)
            action_name = data.get("action", "")
            max_iter = data.get("max_iterations", 0)
            await emit_status(sid, f"Computer Use: paso {iteration}/{max_iter} — {action_name}")

        try:
            from backend.core.computer_use_agent import ComputerUseResult
            cu_result: ComputerUseResult = await self._computer_use_agent.execute_task(
                task,
                target_monitor=target_monitor,
                cancel_event=self._cancel_event,
                on_progress=_on_progress,
                session_id=self._memory.session_id,
                mode_key=self._current_mode,
                parent_task_limit_usd=self._get_current_task_budget_limit(),
            )

            status_emoji = {
                "completed": "OK",
                "failed": "ERROR",
                "cancelled": "CANCELADO",
                "timeout": "TIMEOUT",
            }.get(cu_result.status, cu_result.status.upper())

            await emit_message(
                sid,
                f"Computer Use [{status_emoji}]: {cu_result.summary or cu_result.status}",
                "info" if cu_result.status == "completed" else "warning",
                done=False,
            )

            # Tomar screenshot de verificación post-delegación
            if self._vision:
                try:
                    verify_screen = await self._vision.analyze_screen(
                        mode="computer_use",
                        monitor=target_monitor or int(config.get("vision", "target_monitor", default=0)),
                    )
                    verify_b64 = verify_screen.get("image_base64")
                    if verify_b64:
                        await emit_screenshot(sid, verify_b64)
                        self._memory.add_user_message(
                            "Screenshot de verificación post-delegación adjunto.",
                            images=[verify_b64],
                        )
                except Exception as exc:
                    logger.debug(f"Screenshot de verificación falló: {exc}")

            return cu_result

        except Exception as exc:
            logger.error(f"Error en delegación computer use: {exc}")
            await emit_message(
                sid,
                f"Error en sub-agente computer use: {exc}",
                "error",
                done=False,
            )
            return None

    async def _run_critic_review(self, actions: list[Any], local_review: dict[str, Any]) -> dict[str, Any] | None:
        if not self._router:
            return None
        try:
            critic_review = await self._subagents.review_sensitive_actions(
                router=self._router,
                actions=[
                    {"action": action.type, "params": action.params}
                    for action in actions
                ],
                mode_key="investigador",
                parent_mode_key=self._current_mode,
                session_id=self._memory.session_id,
                parent_task_limit_usd=self._get_current_task_budget_limit(),
                local_review=local_review,
                context_excerpt=self._build_critic_context_excerpt(actions),
            )
            logger.info("=== CRITIC REVIEW START ===")
            logger.info(
                f"decision={critic_review.get('decision')} confidence={critic_review.get('confidence')} "
                f"summary={critic_review.get('summary')}"
            )
            for finding in critic_review.get("findings", []):
                logger.info(
                    f"[CRITIC] action={finding.get('action')} severity={finding.get('severity')} "
                    f"reason={finding.get('reason')}"
                )
            logger.info("=== CRITIC REVIEW END ===")
            return critic_review
        except Exception as exc:
            logger.warning(f"Critic Agent no disponible, se mantiene politica local: {exc}")
            return None

    def _build_action_feedback_prompt(
        self,
        action_feedback_parts: list[str],
        results: list[dict[str, Any]],
    ) -> str:
        feedback_text = render_prompt_text(
            "action_feedback_user",
            fallback=(
                "Resultado de las acciones ejecutadas:\n{action_feedback}\n\n"
                "Continúa con la siguiente acción o usa [ACTION:task_complete(summary=...)] si terminaste."
            ),
            variables={"action_feedback": "\n".join(action_feedback_parts)},
        )

        download_click_detected = False
        file_persistence_failed = False
        file_recovery_hint: dict[str, Any] | None = None
        browser_backend_failed = False
        browser_recovery_hint: dict[str, Any] | None = None
        browser_session_state: dict[str, Any] | None = None
        browser_desktop_handoff: dict[str, Any] | None = None
        screen_ocr_excerpt = ""
        screen_query_match_summary = ""
        screen_extraction_summary = ""
        screen_resolution_summary = ""
        for result in results:
            action_name = str(result.get("action", ""))
            action_msg = _normalize_match_text(result.get("message", ""))
            action_data = result.get("data") or {}
            failure_kind = str(result.get("failure_kind", "") or "").strip().lower()
            readiness = ""
            if isinstance(action_data, dict):
                readiness = str(action_data.get("readiness", "")).strip().lower()
            if action_name in ("browser_click", "browser_force_click") and any(
                kw in action_msg for kw in ["download", "descarga", "descargar", "save", "guardar"]
            ):
                download_click_detected = True
            if action_name == "browser_download_click":
                download_click_detected = True
            if (
                failure_kind in {
                    "file_not_persisted",
                    "file_content_mismatch",
                    "file_append_suffix_mismatch",
                    "file_readback_failed",
                    "replacement_text_unverified",
                }
                or (
                    action_name in {"task_complete", "file_exists", "terminal_run"}
                    and any(
                        marker in action_msg
                        for marker in [
                            "no encontre en disco",
                            "no se encuentra la ruta",
                            "porque no existe",
                            "archivo esperado",
                            "contenido final del archivo no coincide",
                            "no pude releerlo para validar contenido",
                        ]
                    )
                )
            ):
                file_persistence_failed = True
            browser_backend_signal = (
                any(
                    marker in action_msg
                    for marker in [
                        "extension chrome no esta conectada",
                        "extension g-mini agent bridge no conecto",
                        "browser-use no esta instalado",
                        "backend de browser automatizado",
                        "backend estructurado de navegador",
                        "browser automation no disponible",
                        "fallback de escritorio",
                    ]
                )
                or ("chrome" in action_msg and "conect" in action_msg)
                or ("extension" in action_msg and "conect" in action_msg)
            )
            if readiness == "desktop_fallback_ready":
                browser_backend_signal = True
            if action_name.startswith("browser_") and browser_backend_signal:
                browser_backend_failed = True
            if not file_recovery_hint and isinstance(action_data, dict):
                candidate_hint = action_data.get("recovery_hint")
                if isinstance(candidate_hint, dict) and candidate_hint.get("path"):
                    file_recovery_hint = candidate_hint
            if not file_recovery_hint and isinstance(action_data, dict):
                candidate_path = str(
                    action_data.get("verified_path")
                    or action_data.get("path")
                    or ""
                ).strip()
                if not candidate_path:
                    attempts = action_data.get("content_verification_attempts") or []
                    if isinstance(attempts, list) and attempts:
                        first_attempt = attempts[0] or {}
                        if isinstance(first_attempt, dict):
                            candidate_path = str(first_attempt.get("path", "") or "").strip()
                candidate_text = str(
                    action_data.get("expected_content_preview")
                    or ""
                ).strip()
                if candidate_path or candidate_text:
                    file_recovery_hint = {
                        "path": candidate_path,
                        "text": candidate_text,
                    }
            if not browser_recovery_hint and isinstance(action_data, dict):
                candidate_hint = action_data.get("recovery_hint")
                if isinstance(candidate_hint, dict) and candidate_hint.get("kind") == "browser_desktop_fallback":
                    browser_recovery_hint = candidate_hint
                    candidate_handoff = candidate_hint.get("desktop_handoff")
                    if isinstance(candidate_handoff, dict):
                        browser_desktop_handoff = candidate_handoff
            if (
                action_name == "browser_state"
                and isinstance(action_data, dict)
                and str(action_data.get("readiness", "")).strip().lower() == "desktop_fallback_ready"
            ):
                browser_session_state = action_data
                candidate_handoff = action_data.get("desktop_handoff")
                if isinstance(candidate_handoff, dict):
                    browser_desktop_handoff = candidate_handoff
            if (
                not screen_ocr_excerpt
                and action_name in {"screen_read_text", "adb_screen_read_text"}
                and isinstance(action_data, dict)
            ):
                ocr_text = str(action_data.get("ocr_text", action_data.get("text", "")) or "").strip()
                if ocr_text:
                    screen_ocr_excerpt = _smart_truncate_ocr(ocr_text)
                query_match = action_data.get("query_match") or {}
                if isinstance(query_match, dict) and not screen_query_match_summary:
                    query_text = str(query_match.get("query_text", "") or "").strip()
                    best_line = str(query_match.get("best_matching_line", "") or "").strip()
                    confidence = float(query_match.get("match_confidence", 0.0) or 0.0)
                    matched_terms = ", ".join(
                        str(term).strip()
                        for term in (query_match.get("matched_terms") or [])
                        if str(term).strip()
                    )
                    if query_text or best_line:
                        parts = []
                        if query_text:
                            parts.append(f"query={query_text}")
                        if best_line:
                            parts.append(f"mejor_linea={best_line}")
                        parts.append(f"score={confidence:.2f}")
                        if matched_terms:
                            parts.append(f"matched_terms={matched_terms}")
                        screen_query_match_summary = " | ".join(parts)
                extraction = action_data.get("extraction") or {}
                if isinstance(extraction, dict) and not screen_extraction_summary:
                    status = str(extraction.get("status", "") or "").strip()
                    answer_text = str(extraction.get("answer_text", "") or "").strip()
                    anchor_text = str(extraction.get("anchor_text", "") or "").strip()
                    reason = str(extraction.get("reason", "") or "").strip()
                    confidence = float(extraction.get("confidence", 0.0) or 0.0)
                    if status or answer_text or anchor_text:
                        parts = []
                        if status:
                            parts.append(f"status={status}")
                        if answer_text:
                            parts.append(f"respuesta={answer_text}")
                        if anchor_text:
                            parts.append(f"ancla={anchor_text}")
                        parts.append(f"score={confidence:.2f}")
                        if reason:
                            parts.append(f"reason={reason}")
                        screen_extraction_summary = " | ".join(parts)
            if action_name.endswith("_fallback_resolution") and isinstance(action_data, dict) and not screen_resolution_summary:
                status = str(action_data.get("status", "") or "").strip()
                answer_text = str(action_data.get("answer_text", "") or "").strip()
                anchor_text = str(action_data.get("anchor_text", "") or "").strip()
                reason = str(action_data.get("reason", "") or "").strip()
                confidence = float(action_data.get("confidence", 0.0) or 0.0)
                if status or answer_text or anchor_text:
                    parts = []
                    if status:
                        parts.append(f"status={status}")
                    if answer_text:
                        parts.append(f"respuesta={answer_text}")
                    if anchor_text:
                        parts.append(f"ancla={anchor_text}")
                    parts.append(f"score={confidence:.2f}")
                    if reason:
                        parts.append(f"reason={reason}")
                    screen_resolution_summary = " | ".join(parts)

        confirmed_values = _collect_confirmed_action_values(results)

        if download_click_detected:
            feedback_text += (
                "\n\n⚠️ ALERTA: hiciste click en una descarga. "
                "Antes de declarar éxito, verifica archivos reales en disco con browser_check_downloads o downloads_check."
            )
        else:
            feedback_text += "\n\nContinúa con la siguiente acción o usa [ACTION:task_complete(summary=...)] si terminaste."

        if browser_backend_failed:
            browser_target = str((browser_recovery_hint or {}).get("target_profile", "")).strip()
            browser_issue = str((browser_recovery_hint or {}).get("issue", "")).strip()
            browser_extension_path = str((browser_recovery_hint or {}).get("extension_path", "")).strip()
            browser_actions = ", ".join((browser_recovery_hint or {}).get("suggested_actions", []))
            browser_backend = str((browser_session_state or {}).get("backend", "")).strip()
            browser_connection = str((browser_session_state or {}).get("connection", "")).strip()
            browser_hint = str((browser_session_state or {}).get("hint", "")).strip()
            browser_profile_root = str((browser_session_state or {}).get("profile_root", "")).strip()
            browser_open_action = str((browser_desktop_handoff or {}).get("preferred_open_action", "")).strip()
            browser_input_action = str((browser_desktop_handoff or {}).get("preferred_input_action", "")).strip()
            browser_confirm_action = str((browser_desktop_handoff or {}).get("preferred_confirm_action", "")).strip()
            browser_target_strategy = str((browser_desktop_handoff or {}).get("target_url_strategy", "")).strip()
            browser_setup_actions = ", ".join((browser_desktop_handoff or {}).get("setup_actions", []))
            feedback_text += "\n\n" + render_prompt_text(
                "browser_recovery_feedback",
                fallback=(
                    "ALERTA: el backend browser estructurado no esta disponible o no conecto.\n"
                    "No uses terminal_run para abrir URLs ni insistas con browser_* hasta recuperar la sesion.\n"
                    "Haz fallback a Chrome real + computer use/escritorio:\n"
                    "1. Reutiliza o abre Chrome con `chrome_open_profile(...)` o `chrome_open_automation_profile(...)`.\n"
                    "2. Usa `screenshot()` para ubicar la ventana y la barra correcta.\n"
                    "3. Interactua con `click`, `focus_type`, `type`, `press`, `hotkey` y `wait`.\n"
                    "4. Si necesitas browser_* reales, usa computer use para abrir `chrome://extensions` e instalar/cargar la extension desde {extension_path}.\n"
                    "Perfil objetivo: {target_profile}\n"
                    "Backend actual: {backend}\n"
                    "Conexion actual: {connection}\n"
                    "Perfil local: {profile_root}\n"
                    "Motivo del fallback: {issue}\n"
                    "Contexto operativo: {hint}\n"
                    "Acciones sugeridas: {suggested_actions}\n"
                    "Apertura recomendada: {preferred_open_action}\n"
                    "Entrada recomendada: {preferred_input_action}\n"
                    "Confirmacion recomendada: {preferred_confirm_action}\n"
                    "Estrategia de URL: {target_url_strategy}\n"
                    "Acciones de setup: {setup_actions}"
                ),
                variables={
                    "target_profile": browser_target,
                    "backend": browser_backend,
                    "connection": browser_connection,
                    "profile_root": browser_profile_root,
                    "issue": browser_issue,
                    "hint": browser_hint,
                    "extension_path": browser_extension_path,
                    "suggested_actions": browser_actions,
                    "preferred_open_action": browser_open_action,
                    "preferred_input_action": browser_input_action,
                    "preferred_confirm_action": browser_confirm_action,
                    "target_url_strategy": browser_target_strategy,
                    "setup_actions": browser_setup_actions,
                },
            )
            if browser_session_state:
                feedback_text += (
                    "\n\n"
                    f"Sesion fallback activa:\n"
                    f"- backend: {browser_backend or 'desconocido'}\n"
                    f"- conexion: {browser_connection or 'desconocida'}\n"
                    f"- perfil local: {browser_profile_root or 'no disponible'}\n"
                    f"- contexto operativo: {browser_hint or 'sin detalle adicional'}"
                )
            if browser_desktop_handoff:
                suggested_actions = ", ".join(
                    str(item).strip()
                    for item in (browser_desktop_handoff.get("suggested_actions") or [])
                    if str(item).strip()
                )
                feedback_text += (
                    "\n\n"
                    "Contrato de handoff de escritorio:\n"
                    f"- browser_actions_blocked: {bool(browser_desktop_handoff.get('browser_actions_blocked', True))}\n"
                    f"- requires_visual_replan: {bool(browser_desktop_handoff.get('requires_visual_replan', True))}\n"
                    f"- preferred_open_action: {browser_open_action or 'chrome_open_profile'}\n"
                    f"- preferred_input_action: {browser_input_action or 'focus_type'}\n"
                    f"- preferred_confirm_action: {browser_confirm_action or 'press'}\n"
                    f"- target_url_strategy: {browser_target_strategy or 'address_bar'}\n"
                    f"- setup_actions: {browser_setup_actions or 'chrome://extensions, load_unpacked_extension'}\n"
                    f"- suggested_actions: {suggested_actions or 'screenshot, click, focus_type, type, press, wait'}"
                )
        if screen_ocr_excerpt:
            feedback_text += (
                "\n\n"
                "Texto visible capturado por OCR durante el fallback:\n"
                f"{screen_ocr_excerpt}"
            )
        if screen_query_match_summary:
            feedback_text += (
                "\n\n"
                "Verificacion OCR del fallback:\n"
                f"{screen_query_match_summary}"
            )
        if screen_extraction_summary:
            feedback_text += (
                "\n\n"
                "Extraccion OCR estructurada del fallback:\n"
                f"{screen_extraction_summary}"
            )
        if screen_resolution_summary:
            feedback_text += (
                "\n\n"
                "Resolucion final del fallback OCR:\n"
                f"{screen_resolution_summary}"
            )
        if confirmed_values:
            confirmed_lines: list[str] = []
            for entry in confirmed_values[:8]:
                key = str(entry.get("key", "") or "").strip()
                value = str(entry.get("value", "") or "").strip()
                source = str(entry.get("source", "") or "").strip() or "unknown"
                if not key or not value:
                    continue
                line = f"- {key} = {value} (source={source}"
                confidence = entry.get("confidence")
                if confidence is not None:
                    try:
                        line += f", score={float(confidence):.2f}"
                    except (TypeError, ValueError):
                        pass
                line += ")"
                confirmed_lines.append(line)
            if confirmed_lines:
                feedback_text += (
                    "\n\n"
                    "Datos confirmados reutilizables:\n"
                    + "\n".join(confirmed_lines)
                    + "\n"
                    + "Usa estos valores exactos en las siguientes acciones cuando necesites escribir texto, "
                    + "completar plantillas o reemplazar placeholders como {resultado}, {respuesta}, {titulo}, {ruta}, {archivo}, {contenido} o {coincidencia}. "
                    + "No reinfieras ni inventes otro valor si ya existe uno confirmado."
                )

        if file_persistence_failed:
            recovery_path = str((file_recovery_hint or {}).get("path", "")).strip()
            recovery_text = str((file_recovery_hint or {}).get("text", "")).strip()
            feedback_text += "\n\n" + render_prompt_text(
                "file_recovery_feedback",
                fallback=(
                    "ALERTA: la persistencia de un archivo local fallo.\n"
                    "No dependas del cuadro Guardar como ni de atajos de teclado localizados.\n"
                    "Usa `file_write_text(path=..., text=...)` para crear el archivo y luego confirma con "
                    "`file_exists(path=...)` antes de `task_complete`.\n\n"
                    "Accion recomendada: usa persistencia nativa de archivos, no el dialogo Guardar como.\n\n"
                    "Ruta sugerida: {path}\n"
                    "Texto sugerido: {text}"
                ),
                variables={
                    "path": recovery_path,
                    "text": recovery_text,
                },
            )
        feedback_text = (
            feedback_text
            .replace("âš ï¸ ALERTA: hiciste click en una descarga.", "ALERTA: hiciste click en una descarga.")
            .replace("Continúa con la siguiente acción", "Continua con la siguiente accion")
            .replace("Antes de declarar éxito", "Antes de declarar exito")
        )

        return feedback_text

    async def _postprocess_action_results(
        self,
        sid: str,
        results: list[dict[str, Any]],
        *,
        summary_title: str,
    ) -> bool:
        task_completed = False
        action_feedback_parts: list[str] = []
        screenshot_b64 = None
        screenshot_dims = None
        has_action_failures = False
        has_desktop_fallback_guidance = False

        for r in results:
            status_icon = "✓" if r["success"] else "✗"
            action_feedback_parts.append(f"{status_icon} {r['action']}: {r.get('message', '')}")
            if not r["success"]:
                has_action_failures = True
            action_name = str(r.get("action", "") or "").strip()
            data = r.get("data") or {}
            if isinstance(data, dict):
                readiness = str(data.get("readiness", "")).strip().lower()
                if readiness == "desktop_fallback_ready":
                    has_desktop_fallback_guidance = True

            if r.get("task_complete") and r.get("success"):
                task_completed = True
                logger.info(f"✅ Tarea completada: {r.get('message', '')}")

            if (
                action_name in {
                    "screenshot",
                    "browser_screenshot",
                    "adb_screenshot",
                    "adb_preview_start",
                    "screen_preview_start",
                    "adb_wait_for",
                }
                and isinstance(data, dict)
                and data.get("image_base64")
            ):
                screenshot_b64 = str(data["image_base64"])
                screenshot_dims = data.get("screen_dimensions")
                await emit_screenshot(sid, screenshot_b64)

            # Emitir archivos multimedia generados al frontend como reproductor inline
            # rico (preview + zoom + descarga). Una sola tarjeta por archivo: antes se
            # emitia ADEMAS emit_screenshot(generated_image) para imagenes, lo que
            # duplicaba la imagen (tarjeta plana + reproductor).
            if action_name in ("generate_image", "generate_video", "generate_music") and isinstance(data, dict):
                media_type = "image" if action_name == "generate_image" else "video" if action_name == "generate_video" else "audio"
                for gen_file in (data.get("files") or []):
                    fname = gen_file.get("filename", "")
                    if fname:
                        await emit_media(sid, media_type, fname, f"/api/media/{fname}")

        if action_feedback_parts:
            summary = summary_title + "\n" + "\n".join(action_feedback_parts)
            logger.info("=== ACTION FEEDBACK TO MODEL START ===")
            for line in action_feedback_parts:
                logger.info(line)
            logger.info("=== ACTION FEEDBACK TO MODEL END ===")
            # Send as "action" type so the frontend renders it as a system-level
            # activity summary, not as LLM streaming text
            await emit_message(sid, summary, "action", done=False)

            # Persist action feedback for UI history
            await self._memory.persist_display_message(
                content=summary,
                message_type="action",
                metadata={"actions": [
                    {"action": r.get("action", ""), "success": r.get("success", False),
                     "message": r.get("message", "")} for r in results
                ]},
            )

        if screenshot_b64 and not task_completed:
            await self._inject_screenshot_result(screenshot_b64, screenshot_dims)
            logger.info("📸 Screenshot re-inyectado al contexto del LLM")
            if action_feedback_parts and (has_action_failures or has_desktop_fallback_guidance):
                self._memory.add_user_message(
                    self._build_action_feedback_prompt(action_feedback_parts, results)
                )
        elif action_feedback_parts and not task_completed:
            self._memory.add_user_message(
                self._build_action_feedback_prompt(action_feedback_parts, results)
            )

        return task_completed

    def _llm_retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=int(config.get("automation", "llm_retry_attempts", default=2)),
            initial_delay_ms=int(config.get("automation", "retry_initial_delay_ms", default=300)),
            backoff_multiplier=float(config.get("automation", "retry_backoff_multiplier", default=2.0)),
            max_delay_ms=int(config.get("automation", "retry_max_delay_ms", default=2000)),
        )

    @staticmethod
    def _estimate_llm_messages_tokens(messages: list[LLMMessage]) -> int:
        msg_dicts = [
            {
                "role": item.role,
                "content": item.content,
                "images": item.images or [],
            }
            for item in messages
        ]
        return count_messages_tokens(msg_dicts)

    # ------------------------------------------------------------------
    # Context compression for cost optimization (Fase 9.4)
    # ------------------------------------------------------------------

    @staticmethod
    def _should_compress_for_cost(estimated_tokens: int) -> bool:
        threshold = int(config.get("cost_optimization", "compress_context_above_tokens", default=40000) or 40000)
        return estimated_tokens > threshold

    @staticmethod
    def _compress_messages_for_cost(messages: list[LLMMessage]) -> list[LLMMessage]:
        """
        Comprime mensajes manteniendo system prompt, ultimos N mensajes,
        y resumiendo mensajes intermedios para reducir tokens.
        """
        keep_recent = int(config.get("cost_optimization", "compress_keep_recent_messages", default=16) or 16)

        system_msgs = [m for m in messages if m.role == "system"]
        non_system = [m for m in messages if m.role != "system"]

        if len(non_system) <= keep_recent:
            return messages

        old_msgs = non_system[:-keep_recent]
        recent_msgs = non_system[-keep_recent:]

        # Resumir mensajes antiguos en un bloque compacto
        summary_parts: list[str] = []
        for msg in old_msgs:
            content = str(msg.content or "").strip()
            if not content:
                continue
            role_label = "Usuario" if msg.role == "user" else "Agente"
            # Tomar solo la primera linea o 120 chars
            first_line = content.split("\n")[0][:120].strip()
            if first_line:
                summary_parts.append(f"[{role_label}] {first_line}")

        # Limitar resumen a 30 entradas
        summary_parts = summary_parts[-30:]

        if summary_parts:
            summary_content = (
                "[Contexto comprimido por optimización de costos — "
                f"{len(old_msgs)} mensajes resumidos]\n\n"
                + "\n".join(summary_parts)
            )
            compression_msg = LLMMessage(role="system", content=summary_content)
            return system_msgs + [compression_msg] + recent_msgs

        return system_msgs + recent_msgs

    async def _record_llm_usage(
        self,
        *,
        provider: str,
        model: str,
        source: str,
        input_tokens: int,
        output_tokens: int,
        estimated: bool,
        mode_key: str | None = None,
        worker_id: str = "main",
        worker_kind: str = "agent",
        parent_worker_id: str = "",
        parent_task_limit_usd: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._cost_tracker.record_llm_usage(
            session_id=self._memory.session_id,
            provider=provider,
            model=model,
            source=source,
            mode_key=mode_key or self._current_mode,
            worker_id=worker_id,
            worker_kind=worker_kind,
            parent_worker_id=parent_worker_id,
            parent_task_limit_usd=parent_task_limit_usd or self._get_current_task_budget_limit(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated=estimated,
            metadata=metadata,
        )

    def _get_current_task_budget_limit(self, mode_key: str | None = None) -> float:
        target_mode = str(mode_key or self._current_mode or "").strip().lower()
        base_limit = float(
            config.get("model_router", "hard_limits", "max_cost_per_task_usd", default=0.0) or 0.0
        )
        raw_mode_limits = config.get("budget", "mode_task_limits_usd", default={}) or {}
        mode_limits = raw_mode_limits if isinstance(raw_mode_limits, dict) else {}
        mode_limit = 0.0
        if target_mode:
            try:
                mode_limit = float(mode_limits.get(target_mode, 0.0) or 0.0)
            except (TypeError, ValueError):
                mode_limit = 0.0
        if mode_limit > 0:
            return mode_limit
        return base_limit if base_limit > 0 else 0.0

    @staticmethod
    def _build_budget_error_message(budget_status: dict[str, Any]) -> str:
        alerts = budget_status.get("alerts", [])
        if alerts:
            return "\n".join(str(item) for item in alerts if str(item).strip())
        return "Se supero el presupuesto operativo configurado."

    async def _raise_if_budget_exceeded(self, usage_event: dict[str, Any]) -> None:
        budget_status = usage_event.get("budget_status", {})
        if not isinstance(budget_status, dict):
            return
        if not budget_status.get("stop_required"):
            return
        raise BudgetLimitExceeded(self._build_budget_error_message(budget_status))

    async def _generate_llm_response_with_retry(
        self,
        sid: str,
        final_messages: list[LLMMessage],
        model: str,
    ) -> tuple[str, int]:
        if not self._router:
            raise RuntimeError("ModelRouter no inicializado")

        policy = self._llm_retry_policy()
        last_error = "respuesta vacía del LLM"

        # --- Cost optimization: evaluar y aplicar (Fase 9.4) ---
        estimated_input = self._estimate_llm_messages_tokens(final_messages)
        messages_to_send = final_messages

        try:
            from backend.core.cost_optimizer import get_cost_optimizer
            optimizer = get_cost_optimizer()
            opt_result = await optimizer.resolve_model(
                requested_provider=self._router.get_current_provider_name(),
                requested_model=model,
                session_id=self._memory.session_id,
                mode_key=self._current_mode,
                source="agent_loop_stream",
                estimated_input_tokens=estimated_input,
            )
            if opt_result.compress_context and self._should_compress_for_cost(estimated_input):
                messages_to_send = self._compress_messages_for_cost(final_messages)
                logger.info(
                    f"CostOptimizer: contexto comprimido de ~{estimated_input} a "
                    f"~{self._estimate_llm_messages_tokens(messages_to_send)} tokens"
                )
        except Exception as exc:
            logger.debug(f"CostOptimizer pre-check omitido: {exc}")

        emotions_enabled = config.get("character", "emotions_enabled", default=False)

        for attempt in range(1, policy.max_attempts + 1):
            await self._set_agent_status(sid, AgentStatus.RESPONDING)
            full_response = ""
            chunk_count = 0
            emotion_filter = EmotionTagFilter() if emotions_enabled else None

            try:
                async for chunk in self._router.generate_cost_aware(
                    messages_to_send,
                    model=model,
                    temperature=config.get("model_router", "temperature", default=0.7),
                    max_tokens=config.get("model_router", "max_tokens", default=4096),
                    stream=True,
                    session_id=self._memory.session_id,
                    mode_key=self._current_mode,
                    source="agent_loop_stream",
                    estimated_input_tokens=self._estimate_llm_messages_tokens(messages_to_send),
                ):
                    await self._pause_event.wait()
                    if self._cancel_event.is_set():
                        raise asyncio.CancelledError()

                    full_response += chunk
                    chunk_count += 1
                    if emotion_filter:
                        clean_chunk, emotion = emotion_filter.feed(chunk)
                        if emotion:
                            await emit_emotion(sid, emotion)
                        if clean_chunk:
                            await emit_message_chunk(sid, clean_chunk)
                    else:
                        await emit_message_chunk(sid, chunk)

                if emotion_filter:
                    rest = emotion_filter.flush()
                    if rest:
                        await emit_message_chunk(sid, rest)
            except asyncio.CancelledError:
                await emit_message_done(sid)
                logger.info("Generación cancelada")
                raise
            except Exception as llm_exc:
                last_error = str(llm_exc)
                await emit_message_done(sid)
                if attempt >= policy.max_attempts:
                    raise
                delay = policy.delay_seconds(attempt)
                logger.warning(
                    f"Generación LLM falló (intento {attempt}/{policy.max_attempts}): {llm_exc}. "
                    f"Reintentando en {delay:.2f}s"
                )
                await asyncio.sleep(delay)
                continue

            await emit_message_done(sid)
            if self._cancel_event.is_set():
                raise asyncio.CancelledError()

            stripped = full_response.strip()
            if stripped and not stripped.startswith("[Error:"):
                usage_meta = self._router.get_last_generation_meta()
                actual_provider = str(usage_meta.get("provider") or self._router.get_current_provider_name())
                actual_model = str(usage_meta.get("model") or model)
                opt_info = self._router.get_last_optimization()
                metadata: dict[str, Any] = {
                    "chunk_count": chunk_count,
                    "requested_provider": usage_meta.get("requested_provider") or self._router.get_current_provider_name(),
                    "requested_model": usage_meta.get("requested_model") or model,
                    "fallback": bool(usage_meta.get("fallback")),
                }
                if opt_info and getattr(opt_info, "switched", False):
                    metadata["cost_optimized"] = True
                    metadata["original_provider"] = opt_info.original_provider
                    metadata["original_model"] = opt_info.original_model
                    metadata["optimization_reason"] = opt_info.reason

                usage_event = await self._record_llm_usage(
                    provider=actual_provider,
                    model=actual_model,
                    source="agent_loop_stream",
                    input_tokens=self._estimate_llm_messages_tokens(messages_to_send),
                    output_tokens=count_tokens(full_response),
                    estimated=True,
                    mode_key=self._current_mode,
                    worker_id="main",
                    worker_kind="agent",
                    metadata=metadata,
                )
                await self._raise_if_budget_exceeded(usage_event)
                return full_response, chunk_count

            last_error = stripped or "respuesta vacía del LLM"
            if self._cancel_event.is_set():
                raise asyncio.CancelledError()
            if attempt >= policy.max_attempts:
                break

            delay = policy.delay_seconds(attempt)
            logger.warning(
                f"Respuesta LLM inválida o vacía (intento {attempt}/{policy.max_attempts}): {last_error}. "
                f"Reintentando en {delay:.2f}s"
            )
            await asyncio.sleep(delay)

        if self._cancel_event.is_set():
            raise asyncio.CancelledError()
        raise LLMResponseUnavailableError(last_error)

    def _build_progress_fingerprint(self, results: list[dict[str, Any]]) -> str:
        markers: list[str] = []

        for result in results:
            action = str(result.get("action", ""))
            data = result.get("data") or {}
            if isinstance(data, dict):
                url = data.get("url")
                title = data.get("title")
                if url:
                    markers.append(f"url:{url}")
                if title:
                    markers.append(f"title:{title}")

                snapshot_text = data.get("snapshot") or data.get("text") or data.get("content")
                if snapshot_text:
                    digest = hashlib.sha1(str(snapshot_text)[:4000].encode("utf-8", errors="ignore")).hexdigest()[:12]
                    markers.append(f"{action}:text:{digest}")

                image_b64 = data.get("image_base64")
                if image_b64:
                    digest = hashlib.sha1(str(image_b64)[:4096].encode("utf-8", errors="ignore")).hexdigest()[:12]
                    markers.append(f"{action}:image:{digest}")

            if not data:
                markers.append(f"{action}:{result.get('message', '')}")

        return "|".join(markers[:12])

    def _build_stagnation_feedback(self, results: list[dict[str, Any]]) -> str:
        recent_results = "\n".join(
            f"- {item.get('action', '?')}: {item.get('message', '')}"
            for item in results[:8]
        )
        return render_prompt_text(
            "stagnation_feedback",
            fallback=(
                "No hubo progreso observable después de varios intentos recientes.\n"
                "Debes cambiar de estrategia, verificar el estado actual y evitar repetir la misma acción."
            ),
            variables={"recent_results": recent_results},
        )

    @staticmethod
    def _extract_ocr_hash_from_results(results: list[dict[str, Any]]) -> str:
        """Extrae un hash MD5 del texto OCR contenido en los resultados de acciones."""
        for result in results:
            action_name = str(result.get("action", "")).strip()
            if action_name not in {"screen_read_text", "adb_screen_read_text", "screenshot"}:
                continue
            data = result.get("data") or {}
            if not isinstance(data, dict):
                continue
            ocr_text = str(
                data.get("ocr_text", data.get("text", "")) or ""
            ).strip()
            if ocr_text:
                return hashlib.md5(ocr_text.encode("utf-8", errors="ignore")).hexdigest()
        return ""

    async def _execute_approved_actions(self, sid: str) -> None:
        if not self._pending_approval or not self._planner:
            return

        actions = self._pending_approval["actions"]
        self._pending_approval = None
        await emit_approval_state(sid, pending=False)

        await self._set_agent_status(sid, AgentStatus.EXECUTING)
        from backend.core.planner import set_planner_socket
        set_planner_socket(sio, sid)
        await sio.emit("agent:executing", {"active": True}, to=sid)
        try:
            results = await self._planner.execute_actions(actions)
        except Exception as exc:
            logger.exception(f"Error ejecutando acciones aprobadas: {exc}")
            await self._set_agent_status(sid, AgentStatus.IDLE)
            await emit_message(
                sid,
                "⚠️ Ocurrió un error operativo al ejecutar las acciones aprobadas. "
                "La tarea se reanudará solo si vuelves a intentarlo.",
                "warning",
                done=True,
            )
            return
        finally:
            await sio.emit("agent:executing", {"active": False}, to=sid)
        task_completed = await self._postprocess_action_results(
            sid,
            results,
            summary_title="**Acciones aprobadas ejecutadas:**",
        )

        if task_completed:
            await self._set_agent_status(sid, AgentStatus.IDLE)
            await self._emit_activity(sid, "✅ **Tarea completada**", "system")
            return

        max_iterations = config.get("automation", "max_loop_iterations", default=25)
        loop_timeout = config.get("automation", "loop_timeout_seconds", default=300)
        await self._emit_activity(sid, "Reanudando la tarea después de la aprobación.", "system")
        await self._run_agent_loop(sid, max_iterations, loop_timeout)

    @property
    def current_mode(self) -> str:
        return self._current_mode

    def _apply_system_prompt(self) -> None:
        prompt = build_mode_system_prompt(self._base_system_prompt, self._current_mode)

        autonomy = config.get("agent", "autonomy_level", default="supervisado")
        prompt = prompt + f"\n\n[AUTONOMÍA ACTUAL: {autonomy}]"

        prompt = prompt + "\n\n" + build_avatar_context()

        if config.get("character", "emotions_enabled", default=False):
            prompt = prompt + "\n\n" + EMOTION_TAGS_PROMPT

        mcp_context = self._get_mcp_tools_context()
        if mcp_context:
            prompt = prompt + "\n\n" + mcp_context

        self._memory.set_system_prompt(prompt)
        logger.debug(
            f"System prompt aplicado: total_len={len(prompt)}, "
            f"base_len={len(self._base_system_prompt)}, "
            f"mcp_context_len={len(mcp_context) if mcp_context else 0}, "
            f"mode={self._current_mode}, autonomy={autonomy}, "
            f"has_mcpcontrol={'mcpcontrol' in prompt.lower()}"
        )
        logger.trace(
            f"SYSTEM PROMPT APPLIED [len={len(prompt)}]:\n"
            f"--- SYSTEM PROMPT START ---\n{prompt}\n--- SYSTEM PROMPT END ---"
        )

    def _get_mcp_tools_context(self) -> str:
        """Genera contexto MCP para inyectar en el system prompt."""
        if not self._planner:
            logger.debug("_get_mcp_tools_context: planner no disponible")
            return ""
        try:
            runtime = getattr(self._planner, '_mcp_runtime', None)
            if not runtime:
                logger.debug("_get_mcp_tools_context: mcp_runtime no disponible en planner")
                return ""
            summary = runtime.get_all_tools_summary()
            if not summary:
                logger.debug("_get_mcp_tools_context: get_all_tools_summary retornó vacío")
                return ""
            logger.debug(
                f"_get_mcp_tools_context: summary_len={len(summary)}, "
                f"has_mcpcontrol={'mcpcontrol' in summary.lower()}, "
                f"has_chrome={'chrome' in summary.lower()}"
            )
            template_text, _ = get_prompt_text("mcp_tools_context", fallback="")
            if template_text and "{{mcp_tools_summary}}" in template_text:
                result = template_text.replace("{{mcp_tools_summary}}", summary)
                logger.trace(f"MCP TOOLS CONTEXT [len={len(result)}]:\n{result[:500]}...")
                return result
            return summary
        except Exception as exc:
            logger.warning(f"No se pudo generar contexto MCP: {exc}", exc_info=True)
            return ""

    def reload_prompt_configuration(self) -> None:
        self._base_system_prompt = _load_system_prompt()
        self._apply_system_prompt()

    async def reload_voice_configuration(
        self,
        *,
        reload_stt: bool = False,
        origin: str = "unknown",
    ) -> dict[str, Any]:
        raw_tts_primary = config.get("voice", "tts_primary", default="melotts")
        google_key_configured = bool(str(config.get_api_key("google_api") or "").strip())
        elevenlabs_key_configured = bool(
            str(config.get_api_key("elevenlabs_api") or "").strip()
        )
        logger.info(
            "Voice reload requested in AgentCore: "
            f"origin={origin}, "
            f"reload_stt={reload_stt}, "
            f"raw_voice_tts_primary={raw_tts_primary}, "
            f"google_key_configured={google_key_configured}, "
            f"elevenlabs_key_configured={elevenlabs_key_configured}"
        )
        if not self._voice:
            runtime = {
                "requested_engine": "none",
                "active_engine": "none",
                "available": False,
                "reason": "not_available",
                "message": "VoiceEngine no disponible.",
                "warnings": [],
                "supports_numeric_speed": False,
            }
            logger.warning(
                "Voice reload skipped in AgentCore because VoiceEngine is not available: "
                f"origin={origin}, runtime={runtime}"
            )
            return runtime

        await self._voice.reload(reload_stt=reload_stt)
        runtime = self._voice.get_tts_runtime_status()
        logger.info(
            "Voice configuration recargada: "
            f"origin={origin}, "
            f"requested_engine={runtime.get('requested_engine')}, "
            f"active_engine={runtime.get('active_engine')}, "
            f"available={runtime.get('available')}, "
            f"reason={runtime.get('reason')}, "
            f"message={runtime.get('message')}"
        )
        return runtime

    def get_modes(self) -> dict[str, Any]:
        mode = get_mode(self._current_mode)
        return {
            "current_mode": mode.key,
            "current_mode_name": mode.name,
            "current_mode_description": mode.description,
            "current_mode_behavior_prompt": get_mode_behavior_prompt(mode.key),
            "current_mode_system_prompt": mode.system_prompt,
            "current_mode_is_custom": mode.is_custom,
            "allowed_capabilities": list(mode.allowed_capabilities),
            "restricted_capabilities": list(mode.restricted_capabilities),
            "requires_scope_confirmation": mode.requires_scope_confirmation,
            "modes": list_modes(),
        }

    def set_mode(self, mode_key: str) -> dict[str, Any]:
        mode = get_mode(mode_key)
        self._current_mode = mode.key
        config.set("app", "mode", value=mode.key)
        self._memory.set_session_mode(mode.key)
        try:
            asyncio.create_task(self._memory.persist_session_mode())
        except RuntimeError:
            pass
        self._apply_system_prompt()
        return {
            "current_mode": mode.key,
            "current_mode_name": mode.name,
            "description": mode.description,
            "behavior_prompt": get_mode_behavior_prompt(mode.key),
            "system_prompt": mode.system_prompt,
            "is_custom": mode.is_custom,
            "allowed_capabilities": list(mode.allowed_capabilities),
            "restricted_capabilities": list(mode.restricted_capabilities),
            "requires_scope_confirmation": mode.requires_scope_confirmation,
        }

    def list_subagents(self) -> dict[str, Any]:
        items = self._subagents.list_agents()
        return {
            "items": items,
            "active_count": sum(1 for item in items if item.get("status") in {"queued", "running"}),
        }

    def list_terminals(self) -> dict[str, Any]:
        return {
            "shells": self._terminals.list_shells(),
            "sessions": self._terminals.list_sessions(),
            "active_count": self._terminals.active_count(),
            "exec_approvals": self._terminals.get_exec_approvals_summary(),
        }

    @property
    def router(self) -> ModelRouter:
        assert self._router is not None, "AgentCore no inicializado"
        return self._router

    @property
    def sub_orchestrator(self):
        """SubAgentOrchestrator — used by CrewEngine."""
        return self._subagents

    async def initialize(self) -> None:
        """Inicializa todos los subsistemas."""
        logger.info("Inicializando AgentCore...")

        # Router de proveedores LLM
        self._router = ModelRouter()

        # Memoria
        await self._memory.initialize()
        await self._terminals.initialize()
        await self._cost_tracker.initialize()

        # System prompt — carga desde archivo externo (configurable)
        self._base_system_prompt = _load_system_prompt()
        self._current_mode = config.get("app", "mode", default=DEFAULT_MODE_KEY)
        self._memory.set_session_mode(self._current_mode)
        self._apply_system_prompt()

        # Phase 2: Vision + Automation
        try:
            if self._vision:
                await self._vision.initialize()
            if self._ui_detector:
                await self._ui_detector.initialize()
            if self._automation:
                await self._automation.initialize()
            if self._adb:
                await self._adb.initialize()
            if self._browser:
                await self._browser.initialize()
            if self._automation and self._adb and self._vision and ActionPlanner:
                self._planner = ActionPlanner(
                    self._automation,
                    self._adb,
                    self._vision,
                    self._browser,
                    self._terminals,
                    self._workspace,
                    self._ide,
                    self._editor_bridge,
                )
            logger.info("Phase 2 (Vision + Automation) inicializado")
        except Exception as e:
            logger.warning(f"Phase 2 parcialmente disponible: {e}")
            if not self._planner and self._automation and self._adb and self._vision and ActionPlanner:
                try:
                    self._planner = ActionPlanner(
                        self._automation,
                        self._adb,
                        self._vision,
                        self._browser,
                        self._terminals,
                        self._workspace,
                        self._ide,
                        self._editor_bridge,
                    )
                except Exception:
                    pass

        # Phase 2b: Computer Use Sub-Agent
        if self._vision and self._automation and bool(config.get("computer_use", "enabled", default=True)):
            try:
                from backend.core.computer_use_agent import ComputerUseAgent
                self._computer_use_agent = ComputerUseAgent(
                    vision=self._vision,
                    automation=self._automation,
                )
                await self._computer_use_agent.initialize()
                logger.info("Phase 2b (Computer Use Sub-Agent) inicializado")
            except Exception as exc:
                logger.warning(f"Computer Use sub-agent no disponible: {exc}")
                self._computer_use_agent = None

        # Phase 3: Voice
        try:
            if self._voice:
                await self._voice.initialize()
                logger.info(f"Phase 3 (Voice) inicializado — TTS: {self._voice.tts_engine_name}")
            else:
                logger.info("Phase 3 (Voice) no disponible")
        except Exception as e:
            logger.warning(f"Phase 3 parcialmente disponible: {e}")

        self._running = True
        self._cancel_event.clear()

        # Phase 4: MCP auto-discovery — descubre tools y re-aplica system prompt con contexto MCP
        if self._planner and bool(config.get("mcp", "enabled", default=True)):
            try:
                import asyncio
                mcp_summary = await asyncio.to_thread(
                    lambda: getattr(self._planner, '_mcp_runtime', None) and self._planner._mcp_runtime.get_all_tools_summary()
                )
                if mcp_summary:
                    self._apply_system_prompt()
                    logger.info("MCP auto-discovery completado — tools inyectadas en system prompt")
                else:
                    logger.info("MCP auto-discovery: sin tools disponibles")
            except Exception as exc:
                logger.warning(f"MCP auto-discovery falló (no crítico): {exc}")

        logger.info("AgentCore inicializado correctamente")

    async def process_message(
        self,
        sid: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        async with self._session_context_lock:
            await self._process_message_core(sid, text, attachments)

    async def _process_message_core(
        self,
        sid: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Procesa un mensaje del usuario con LOOP AUTÓNOMO:
        1. Agregar mensaje a memoria
        2. Loop: LLM → acciones → re-inyectar resultados → LLM
        3. Continúa hasta task_complete o límite de iteraciones
        """
        if not self._running:
            await emit_message(sid, "El agente está detenido.", "error", done=True)
            return

        if self._paused:
            await emit_message(sid, "El agente está pausado.", "warning", done=True)
            return

        # Reset cancel
        self._cancel_event.clear()
        self._active_sid = sid

        if await self._handle_pending_approval(sid, text):
            return

        # Configuración del loop
        max_iterations = config.get("automation", "max_loop_iterations", default=25)
        loop_timeout = config.get("automation", "loop_timeout_seconds", default=300)

        try:
            # 1. Detectar si parece una tarea que requiere acciones
            task_keywords = ["abre", "abrí", "abrir", "busca", "buscar", "escribe", "escribir",
                           "click", "haz", "ve a", "ir a", "navega", "encuentra", "ejecuta",
                           "cierra", "cerrar", "mueve", "copia", "pega", "descarga", "instala",
                           "open", "search", "go to", "browse", "write", "type", "find"]
            browser_keywords = ["chrome", "google", "tiktok", "youtube", "twitter", "facebook",
                              "instagram", "reddit", "web", "pagina", "página", "sitio", "url",
                              "navega", "browser", "perfil", "busca en", "descarga video",
                              "descarga el", "descargar", "spotify", "netflix", "amazon"]
            text_lower = text.lower()
            is_task_request = any(kw in text_lower for kw in task_keywords)
            is_browser_task = any(kw in text_lower for kw in browser_keywords)

            # 2. Añadir mensaje del usuario a memoria (con hint si es tarea)
            if is_browser_task:
                enhanced_text = render_prompt_text(
                    "browser_task_hint",
                    fallback=(
                        "{user_request}\n\n"
                        "[SISTEMA: Esta es una tarea de navegador. Prioriza browser_* cuando el backend estructurado este disponible.\n"
                        "Flujo preferido:\n"
                        "1. browser_use_profile(query=...) para un perfil Chrome existente, o browser_use_automation_profile() para un perfil limpio.\n"
                        "2. browser_navigate(url=...), browser_click/browser_type/browser_press.\n"
                        "3. browser_snapshot(), browser_extract() o browser_page_info() para verificar.\n"
                        "Si browser_* falla porque no hay browser-use o la extension no esta conectada, NO saltes a terminal_run para abrir URLs.\n"
                        "Haz fallback de computer use/escritorio con chrome_open_profile(...) o chrome_open_automation_profile(), luego screenshot(), click/focus_type/type/press/wait.\n"
                        "Si hace falta, puedes usar computer use para abrir chrome://extensions e instalar la extension desde assets/extension en el perfil deseado.]"
                    ),
                    variables={"user_request": text},
                )
            elif is_task_request:
                enhanced_text = render_prompt_text(
                    "task_request_hint",
                    fallback=(
                        "{user_request}\n\n"
                        "[SISTEMA: Usa [ACTION:screenshot()] para ver la pantalla y luego ejecuta las acciones necesarias. "
                        "Si el objetivo final es crear o guardar un archivo local verificable, prioriza "
                        "file_write_text(path=..., text=...) y confirma con file_exists(path=...) antes de task_complete. "
                        "Si una tarea web no tiene backend browser disponible, degrada a Chrome real + screenshot + acciones de escritorio/computer use en vez de usar terminal_run para abrir URLs.]"
                    ),
                    variables={"user_request": text},
                )
            else:
                enhanced_text = text

            attachments_context, attachment_images = self._build_attachment_context(attachments)
            memory_text = enhanced_text
            persisted_text = text
            if attachments_context:
                memory_text = f"{memory_text}\n\n{attachments_context}"
                persisted_text = f"{persisted_text}\n\n{attachments_context}"

            if attachment_images:
                self._memory.add_message_with_image("user", memory_text, attachment_images)
            else:
                self._memory.add_user_message(memory_text)
            await self._memory.persist_message("user", persisted_text)

            if await self._handle_subagent_request(sid, text):
                return

            if not is_task_request and await self._maybe_auto_delegate(sid, text):
                return

            # 3. Loop autónomo
            await self._run_agent_loop(sid, max_iterations, loop_timeout, is_task_request=is_task_request)

        except asyncio.CancelledError:
            logger.info("Tarea de procesamiento cancelada")
        except BudgetLimitExceeded as exc:
            logger.warning(f"Presupuesto operativo excedido: {exc}")
            await self._emit_activity(
                sid,
                f"⚠️ Presupuesto excedido. {exc}",
                "warning",
            )
        except LLMProviderUnavailableError as exc:
            logger.error(f"Todos los proveedores LLM fallaron: {exc}")
            await self._set_agent_status(sid, AgentStatus.ERROR)
            tried = ", ".join(exc.providers_tried) if exc.providers_tried else "ninguno"
            await self._emit_activity(
                sid,
                f"⚠️ No se pudo conectar con ningún proveedor de IA. "
                f"Providers intentados: {tried}. "
                "Verifica tus API keys en Settings o cambia de modelo/proveedor.",
                "error",
            )
        except ValueError as exc:
            err_msg = str(exc)
            if "Live API" in err_msg or "live-only" in err_msg.lower():
                logger.warning(f"Modelo live-only usado en chat texto: {exc}")
                await self._set_agent_status(sid, AgentStatus.ERROR)
                await self._emit_activity(sid, f"⚠️ {err_msg}", "error")
            else:
                logger.error(f"Error procesando mensaje: {exc}")
                await self._emit_activity(sid, f"Error: {err_msg}", "error")
        except Exception as e:
            logger.error(f"Error procesando mensaje: {e}")
            await self._emit_activity(sid, f"Error: {str(e)}", "error")
        finally:
            await self._set_agent_status(sid, AgentStatus.IDLE)
            if self._active_sid == sid:
                self._active_sid = ""

    async def _run_agent_loop(
        self,
        sid: str,
        max_iterations: int,
        timeout_seconds: float,
        is_task_request: bool = True,
    ) -> None:
        """
        Loop autónomo del agente.
        Ejecuta: LLM → acciones → resultados → LLM hasta completar.
        """
        import time

        start_time = time.time()
        iteration = 0
        task_completed = False
        last_progress_fingerprint = ""
        stagnation_count = 0
        internal_failures = 0
        consecutive_no_action_iterations = 0
        last_ocr_hash = ""
        same_ocr_count = 0
        stagnation_threshold = int(config.get("automation", "stagnation_threshold", default=3))
        internal_error_limit = int(config.get("automation", "internal_error_retry_limit", default=3))
        budget_warning_emitted = False
        budget_model_switched = False

        while iteration < max_iterations and not task_completed:
            iteration += 1
            elapsed = time.time() - start_time

            if elapsed > timeout_seconds:
                logger.warning(f"Loop timeout alcanzado ({timeout_seconds}s)")
                await emit_message(sid, f"⚠️ Timeout: La tarea tomó más de {timeout_seconds}s", "warning", done=True)
                break

            if self._cancel_event.is_set():
                logger.info("Loop cancelado por el usuario")
                break

            await self._pause_event.wait()
            
            if self._cancel_event.is_set():
                logger.info("Loop cancelado por el usuario tras pausa")
                break

            try:
                logger.info(f"🔄 Loop iteración {iteration}/{max_iterations}")
                await self._set_agent_status(sid, AgentStatus.THINKING)

                llm_messages = self._memory.get_llm_messages()
                model = self._router.get_current_model()
                provider_name = self._router.get_current_provider_name()
                msg_dicts = [{"role": m.role, "content": m.content, "images": m.images or []} for m in llm_messages]
                truncated = truncate_messages(msg_dicts, model)
                if len(truncated) < len(msg_dicts):
                    dropped = len(msg_dicts) - len(truncated)
                    logger.info(f"Context truncado: {dropped} mensajes descartados antes de LLM call")
                final_messages = [
                    LLMMessage(role=m["role"], content=m["content"], images=m.get("images") or [])
                    for m in truncated
                ]
                logger.info(
                    f"LLM request preparado | provider={provider_name} model={model} "
                    f"messages={len(final_messages)}"
                )

                # Pre-call budget check: fail fast before spending tokens
                pre_summary = await self._cost_tracker.get_summary(
                    session_id=self._memory.session_id,
                    current_mode=self._current_mode,
                    worker_id="main",
                    worker_kind="agent",
                    parent_task_limit_usd=self._get_current_task_budget_limit(),
                )
                pre_budget = pre_summary.get("budget_status", {})
                if isinstance(pre_budget, dict) and pre_budget.get("stop_required"):
                    # Try auto-switch to cheaper model before giving up
                    if not budget_model_switched:
                        cheaper = self._cost_tracker.recommend_cheaper_model(model)
                        if cheaper and cheaper != model:
                            await emit_message(
                                sid,
                                f"⚠️ Presupuesto excedido. Auto-switch: {model} → {cheaper}",
                                "warning",
                                done=False,
                            )
                            logger.warning(f"Budget auto-switch: {model} → {cheaper}")
                            model = cheaper
                            budget_model_switched = True
                            # Re-check budget is not needed — we switched to cheaper, let it try
                        else:
                            raise BudgetLimitExceeded(self._build_budget_error_message(pre_budget))
                    else:
                        raise BudgetLimitExceeded(self._build_budget_error_message(pre_budget))

                # Proactive budget warning at 80% — emit once per loop
                if not budget_warning_emitted and isinstance(pre_budget, dict):
                    pre_alerts = pre_budget.get("alerts", [])
                    if pre_alerts and not pre_budget.get("stop_required"):
                        alert_text = "⚠️ Presupuesto: " + "; ".join(
                            str(a) for a in pre_alerts if str(a).strip()
                        )
                        await emit_message(sid, alert_text, "warning", done=False)
                        logger.warning(f"Budget warning emitido: {alert_text}")
                        budget_warning_emitted = True

                full_response, chunk_count = await self._generate_llm_response_with_retry(
                    sid=sid,
                    final_messages=final_messages,
                    model=model,
                )
                internal_failures = 0

                self._memory.add_assistant_message(full_response)
                await self._memory.persist_message("assistant", full_response)

                logger.info(f"Respuesta: {chunk_count} chunks, {count_tokens(full_response)} tokens")
                logger.info("=== LLM RAW RESPONSE START ===")
                for line in full_response.splitlines():
                    logger.info(line)
                if not full_response.strip():
                    logger.info("[respuesta vacia]")
                logger.info("=== LLM RAW RESPONSE END ===")

                if not self._planner:
                    logger.warning("Planner no disponible, terminando loop")
                    break

                actions = self._planner.parse_actions(full_response)

                # --- Computer Use: bloquear acciones desktop directas del agente principal ---
                _BLOCKED_DESKTOP_ACTIONS = {
                    "click", "double_click", "right_click", "type", "focus_type",
                    "press", "key", "hotkey", "scroll", "move", "drag",
                }
                desktop_found = [a for a in actions if a.type in _BLOCKED_DESKTOP_ACTIONS]
                if desktop_found:
                    blocked_names = ", ".join(a.type for a in desktop_found)
                    logger.warning(f"Acciones desktop bloqueadas en agente principal: {blocked_names}")
                    self._memory.add_user_message(
                        f"No puedes ejecutar acciones de escritorio directamente ({blocked_names}). "
                        "Usa [ACTION:delegate_computer_use(task=descripcion de la tarea)] "
                        "para delegar interacciones de UI al sub-agente de computer use."
                    )
                    actions = [a for a in actions if a.type not in _BLOCKED_DESKTOP_ACTIONS]

                # --- Computer Use: manejar delegate_computer_use ---
                cu_actions = [a for a in actions if a.type == "delegate_computer_use"]
                other_actions = [a for a in actions if a.type != "delegate_computer_use"]
                for cu_action in cu_actions:
                    cu_task = str(cu_action.params.get("task", "")).strip()
                    cu_monitor = int(cu_action.params.get("monitor", 0) or 0)
                    if cu_task:
                        cu_result = await self._handle_computer_use_delegation(
                            sid, cu_task, cu_monitor
                        )
                        if cu_result:
                            self._memory.add_user_message(
                                f"Resultado de delegacion computer use:\n"
                                f"Estado: {cu_result.status}\n"
                                f"Resumen: {cu_result.summary}\n"
                                f"Iteraciones: {cu_result.iterations_used}\n"
                                f"Acciones: {', '.join(cu_result.action_history[-5:]) if cu_result.action_history else 'ninguna'}"
                                + (f"\nError: {cu_result.error}" if cu_result.error else "")
                            )
                actions = other_actions

                if not actions:
                    consecutive_no_action_iterations += 1
                    if iteration == 1 and is_task_request:
                        reinforcement = render_prompt_text(
                            "no_actions_reinforcement",
                            fallback=(
                                "No emitiste acciones [ACTION:...]. "
                                "Debes actuar sobre el entorno, verificar el estado y continuar con acciones concretas."
                            ),
                        )
                        logger.warning("Primera iteración sin acciones en tarea operativa - inyectando refuerzo")
                        self._memory.add_user_message(reinforcement)
                        await asyncio.sleep(0.2)
                        continue

                    if consecutive_no_action_iterations >= 2:
                        # Implicit task_complete: el LLM lleva 2 respuestas sin emitir acciones
                        summary = full_response.strip()[:300] if full_response.strip() else "Tarea finalizada implicitamente."
                        logger.info(
                            f"Task implicitly completed: {consecutive_no_action_iterations} "
                            "iteraciones consecutivas sin acciones"
                        )
                        task_completed = True
                        break

                    logger.info("Sin acciones detectadas, terminando loop")
                    break
                else:
                    consecutive_no_action_iterations = 0

                review = self._policy.review_actions(actions, mode_key=self._current_mode)
                if review.get("blocked"):
                    findings_text = "\n".join(
                        f"- {item['action']}: {item['reason']}"
                        for item in review.get("findings", [])
                    )
                    logger.warning("=== POLICY BLOCK START ===")
                    logger.warning(findings_text)
                    logger.warning("=== POLICY BLOCK END ===")
                    await emit_message(
                        sid,
                        "Estas acciones fueron bloqueadas por la politica del modo activo:\n"
                        f"{findings_text}\n\n"
                        "Cambia de modo o ajusta la tarea antes de continuar.",
                        "warning",
                        done=True,
                    )
                    break

                critic_review = None
                critic_decision = ""
                critic_confidence = 0.0
                critic_findings: list[dict[str, Any]] = []
                critic_summary = ""
                autonomy_level = review.get("autonomy_level", "supervisado")
                highest_threshold = float(review.get("highest_threshold", 0.0))
                if review.get("requires_critic"):
                    critic_review = await self._run_critic_review(actions, review)
                    if critic_review:
                        review["critic"] = critic_review
                        critic_decision = str(critic_review.get("decision", "approve")).strip().lower()
                        critic_confidence = float(critic_review.get("confidence", 0.0))
                        critic_findings = critic_review.get("findings", [])
                        critic_summary = str(critic_review.get("summary", "")).strip()

                        if critic_findings:
                            review["findings"] = critic_findings

                        highest_threshold = max(
                            highest_threshold,
                            max(
                                (float(item.get("threshold", 0.0)) for item in review.get("findings", [])),
                                default=0.0,
                            ),
                        )
                        review["highest_threshold"] = highest_threshold

                        if critic_decision == "deny":
                            review["blocked"] = True
                            review["requires_approval"] = False
                            findings_text = "\n".join(
                                f"- {item['action']}: {item['reason']}"
                                for item in review.get("findings", [])
                            ) or f"- critic_review: {critic_summary}"
                            logger.warning("=== CRITIC BLOCK START ===")
                            logger.warning(findings_text)
                            logger.warning("=== CRITIC BLOCK END ===")
                            await emit_message(
                                sid,
                                "El Critic Agent bloqueó la ejecución de este plan:\n"
                                f"{findings_text}\n\n"
                                f"Resumen: {critic_summary}",
                                "warning",
                                done=True,
                            )
                            break

                        auto_dry_run = bool(
                            config.get("agent", "auto_dry_run_below_threshold", default=True)
                        )
                        should_dry_run = critic_decision == "dry_run" or (
                            auto_dry_run and highest_threshold > 0.0 and critic_confidence < highest_threshold
                        )
                        if should_dry_run:
                            review["requires_dry_run"] = True
                            review["requires_approval"] = True
                            review["dry_run_summary"] = self._build_dry_run_preview(actions, review)
                        else:
                            review["requires_dry_run"] = False
                        approval_override = str(review.get("approval_override", "")).strip().lower()
                        if not should_dry_run and approval_override == "require":
                            review["requires_approval"] = True
                        elif not should_dry_run and approval_override == "skip":
                            review["requires_approval"] = False
                        elif not should_dry_run and critic_decision == "approve":
                            review["requires_approval"] = True
                        elif not should_dry_run and critic_decision == "allow" and critic_confidence >= highest_threshold:
                            review["requires_approval"] = autonomy_level == "asistido"
                        elif not should_dry_run:
                            review["requires_approval"] = autonomy_level != "libre"

                if review.get("requires_approval"):
                    approval_kind = "dry_run" if review.get("requires_dry_run") else "approval"
                    critic_suffix = ""
                    if review.get("critic"):
                        critic_data = review["critic"]
                        critic_suffix = (
                            f"\nCritic Agent: {critic_data.get('decision')} "
                            f"(confianza {critic_data.get('confidence', 0.0):.2f}) - "
                            f"{critic_data.get('summary', '')}"
                        )
                    self._pending_approval = {
                        "actions": actions,
                        "review": review,
                        "assistant_response": full_response,
                        "kind": approval_kind,
                        "sid": sid,
                        "session_id": self._memory.session_id,
                        "token": secrets.token_urlsafe(8),
                    }
                    findings_text = "\n".join(
                        f"- {item.get('action', '?')} ({item.get('severity', '?')}, score {item.get('confidence', 0.0):.2f}/{item.get('threshold', 0.0):.2f}): {item.get('reason', '?')}"
                        for item in review.get("findings", [])
                    )
                    logger.warning("=== APPROVAL REQUIRED START ===")
                    approval_summary = (
                        review.get("dry_run_summary", "")
                        if approval_kind == "dry_run"
                        else (
                            "Estas acciones requieren aprobacion antes de ejecutarse:\n"
                            f"{findings_text}"
                            f"{critic_suffix}"
                        )
                    )
                    logger.warning(approval_summary)
                    logger.warning("=== APPROVAL REQUIRED END ===")
                    await emit_approval_state(
                        sid,
                        pending=True,
                        summary=approval_summary,
                        findings=review.get("findings", []),
                        mode=review.get("mode"),
                        mode_name=review.get("mode_name"),
                        kind=approval_kind,
                        decision=critic_decision or None,
                    )
                    approval_prompt = (
                        "Responde 'aprobar' para ejecutar de verdad o 'cancelar' para descartarlas."
                        if approval_kind == "dry_run"
                        else "Responde 'aprobar' para continuar o 'cancelar' para descartarlas."
                    )
                    await emit_message(
                        sid,
                        f"{approval_summary}\n\n{approval_prompt}",
                        "warning",
                        done=True,
                    )
                    break

                await self._set_agent_status(sid, AgentStatus.EXECUTING)
                logger.info(f"Ejecutando {len(actions)} acciones...")

                from backend.core.planner import set_planner_socket

                set_planner_socket(sio, sid)
                await sio.emit("agent:executing", {"active": True}, to=sid)
                try:
                    results = await self._planner.execute_actions(actions)
                finally:
                    await sio.emit("agent:executing", {"active": False}, to=sid)

                task_completed = await self._postprocess_action_results(
                    sid,
                    results,
                    summary_title="**Acciones ejecutadas:**",
                ) or task_completed

                progress_fingerprint = self._build_progress_fingerprint(results)
                if progress_fingerprint and progress_fingerprint == last_progress_fingerprint and not task_completed:
                    stagnation_count += 1
                    logger.warning(
                        f"Se detectó estancamiento operativo ({stagnation_count}/{stagnation_threshold}) "
                        f"con fingerprint={progress_fingerprint[:80]}"
                    )
                else:
                    stagnation_count = 0

                if progress_fingerprint:
                    last_progress_fingerprint = progress_fingerprint

                # Detección adaptativa de estancamiento por hash OCR
                current_ocr_hash = self._extract_ocr_hash_from_results(results)
                if current_ocr_hash:
                    if current_ocr_hash == last_ocr_hash:
                        same_ocr_count += 1
                        if same_ocr_count >= 2 and not task_completed:
                            # La pantalla no cambió en 2+ iteraciones — estancamiento visual
                            effective_stagnation = max(stagnation_count, same_ocr_count)
                            if effective_stagnation >= stagnation_threshold:
                                stagnation_feedback = self._build_stagnation_feedback(results)
                                self._memory.add_user_message(stagnation_feedback)
                                await emit_message(
                                    sid,
                                    "⚠️ Detecté que la pantalla no cambió tras varias acciones. "
                                    "Forzando replanificación con otra estrategia.",
                                    "warning",
                                    done=False,
                                )
                                stagnation_count = 0
                                same_ocr_count = 0
                                last_ocr_hash = ""
                    else:
                        same_ocr_count = 0
                    last_ocr_hash = current_ocr_hash

                if not task_completed and stagnation_count >= stagnation_threshold:
                    stagnation_feedback = self._build_stagnation_feedback(results)
                    self._memory.add_user_message(stagnation_feedback)
                    await emit_message(
                        sid,
                        "⚠️ Detecté que la tarea no está progresando. Voy a forzar una replanificación con otra estrategia.",
                        "warning",
                        done=False,
                    )
                    stagnation_count = 0

                await asyncio.sleep(0.2)

            except asyncio.CancelledError:
                logger.info("Loop cancelado por el usuario")
                break
            except LLMProviderUnavailableError:
                # Propagar al handler superior de _process_message_core
                raise
            except LLMResponseUnavailableError as exc:
                internal_failures += 1
                logger.warning(
                    "Respuesta LLM no disponible tras reintentos "
                    f"({internal_failures}/{internal_error_limit}): {exc}"
                )
                await emit_message(
                    sid,
                    f"⚠️ El modelo no respondió correctamente ({internal_failures}/{internal_error_limit}). Reintentando.",
                    "warning",
                    done=False,
                )
                if internal_failures >= internal_error_limit:
                    await emit_message(
                        sid,
                        "⚠️ El modelo sigue devolviendo respuestas vacías o inválidas. "
                        "Cambia de modelo/proveedor o reintenta la tarea.",
                        "warning",
                        done=True,
                    )
                    break
                await asyncio.sleep(min(2.0, 0.5 * internal_failures))
                continue
            except BudgetLimitExceeded as exc:
                logger.warning(f"Loop detenido por presupuesto: {exc}")
                await emit_message(
                    sid,
                    f"⚠️ Presupuesto operativo excedido. {exc}",
                    "warning",
                    done=True,
                )
                break
            except Exception as exc:
                internal_failures += 1
                logger.exception(f"Fallo operativo en el loop autónomo: {exc}")
                self._memory.add_user_message(
                    "Se produjo un error operativo interno en la iteración anterior. "
                    "Replanifica con una estrategia distinta, verifica el estado actual y evita repetir la misma acción."
                )
                await emit_message(
                    sid,
                    f"⚠️ Recuperando de un error operativo interno ({internal_failures}/{internal_error_limit}).",
                    "warning",
                    done=False,
                )
                if internal_failures >= internal_error_limit:
                    await emit_message(
                        sid,
                        "⚠️ Se alcanzó el límite de recuperación automática del loop. "
                        "Revisa el estado actual antes de reintentar.",
                        "warning",
                        done=True,
                    )
                    break
                await asyncio.sleep(min(2.0, 0.5 * internal_failures))
                continue

        if task_completed:
            await self._emit_activity(sid, "✅ **Tarea completada**", "system")
        elif iteration >= max_iterations:
            await self._emit_activity(sid, f"⚠️ Límite de {max_iterations} iteraciones alcanzado", "warning")

        await self._set_agent_status(sid, AgentStatus.IDLE)
        await self._maybe_synthesize_tts(sid, self._memory.get_last_assistant_message())

    async def _inject_screenshot_result(self, image_base64: str, screen_dims: dict | None = None) -> None:
        """Re-inyecta un screenshot al contexto del LLM como mensaje con imagen."""
        dims_info = ""
        if screen_dims:
            sent_w = screen_dims.get("sent_w", 0)
            sent_h = screen_dims.get("sent_h", 0)

            if sent_w > 0 and sent_h > 0:
                dims_info = (
                    f"\n\nIMPORTANTE — COORDENADAS: Esta imagen mide {sent_w}x{sent_h} píxeles. "
                    f"Cuando uses click, double_click, right_click o focus_type, las coordenadas (x, y) "
                    f"deben ser posiciones en píxeles DENTRO DE ESTA IMAGEN (esquina superior izquierda = 0,0). "
                    f"El sistema escalará automáticamente las coordenadas a la pantalla real del usuario. "
                    f"NO intentes adivinar la resolución real; usa solo las coordenadas de la imagen que ves."
                )

        feedback = render_prompt_text(
            "screenshot_feedback",
            fallback=(
                "Aquí está la captura de pantalla actual. Analiza qué ves y continúa con la tarea."
                "{dims_info}\nSi ya terminaste, usa [ACTION:task_complete(summary=...)]"
                "\nPara tareas web, prefiere browser_* si el backend estructurado esta disponible. "
                "Si no lo esta, usa fallback de escritorio/computer use con screenshot(), click, focus_type, press y wait."
            ),
            variables={"dims_info": dims_info},
        )
        # Crear mensaje con imagen
        self._memory.add_message_with_image("user", feedback, [image_base64])

    async def _maybe_synthesize_tts(self, sid: str, text: str | None) -> None:
        """Sintetiza TTS si está habilitado."""
        if not text:
            return

        tts_enabled = config.get("voice", "auto_tts", default=False)
        if not tts_enabled or not self._voice or not self._voice.tts_available:
            return

        try:
            import re
            clean_text = re.sub(r'\[ACTION:.*?\]', '', text).strip()
            clean_text, _ = extract_emotion_tags(clean_text)
            if clean_text:
                audio = await self._voice.synthesize(clean_text[:500])
                if audio:
                    import base64
                    audio_b64 = base64.b64encode(audio).decode("utf-8")
                    await sio.emit(
                        "agent:audio",
                        {"audio": audio_b64, "format": self._voice.tts_output_format},
                        to=sid,
                    )

                    lipsync = self._voice.generate_lipsync_data(audio)
                    if lipsync:
                        await sio.emit("agent:lipsync", {"visemes": lipsync}, to=sid)
        except Exception as e:
            logger.warning(f"TTS error (non-blocking): {e}")

    async def stop(self) -> None:
        """Detiene la generación actual."""
        self._cancel_event.set()
        self._paused = False
        self._pause_event.set()
        # Cancelar operaciones de media en curso
        try:
            from backend.providers.google_media import cancel_media_generation
            cancel_media_generation()
        except Exception:
            pass
        await self._stop_planner_previews(context="al hacer stop")
        logger.info("AgentCore: Stop solicitado")
        if self._active_sid:
            await self._set_agent_status(self._active_sid, AgentStatus.IDLE)

    async def shutdown(self) -> None:
        """Libera recursos de runtime durante el apagado del backend."""
        await self._stop_planner_previews(context="durante shutdown")

        if getattr(self, "_realtime_voice", None):
            try:
                await self._realtime_voice.stop_session()
            except Exception as exc:
                logger.warning(f"No se pudo detener realtime voice durante shutdown: {exc}")

        if getattr(self, "_browser", None) and hasattr(self._browser, "shutdown"):
            try:
                await self._browser.shutdown()
            except Exception as exc:
                logger.warning(f"No se pudo cerrar browser controller durante shutdown: {exc}")

        if getattr(self, "_vision", None) and hasattr(self._vision, "shutdown"):
            try:
                await self._vision.shutdown()
            except Exception as exc:
                logger.warning(f"No se pudo cerrar vision engine durante shutdown: {exc}")

        logger.info("AgentCore: Shutdown completado")

    async def _stop_planner_previews(self, *, context: str) -> None:
        planner = getattr(self, "_planner", None)
        if not planner:
            return

        preview_methods = (
            ("stop_desktop_preview", "desktop preview"),
            ("stop_adb_preview", "Android preview"),
        )

        for method_name, label in preview_methods:
            stop_method = getattr(planner, method_name, None)
            if not callable(stop_method):
                continue
            try:
                await stop_method()
            except Exception as exc:
                logger.warning(f"No se pudo detener {label} {context}: {exc}")

    async def pause(self) -> None:
        """Pausa la generación."""
        if self._paused:
            return
        self._status_before_pause = self._last_status if self._last_status != AgentStatus.PAUSED else AgentStatus.RESPONDING
        self._paused = True
        self._pause_event.clear()
        logger.info("AgentCore: Pausa activada")
        if self._active_sid:
            await self._set_agent_status(self._active_sid, AgentStatus.PAUSED)

    async def resume(self) -> None:
        """Reanuda la generación."""
        was_paused = self._paused
        self._paused = False
        self._pause_event.set()
        logger.info("AgentCore: Reanudado")
        if was_paused and self._active_sid:
            resumed_status = self._status_before_pause
            if resumed_status in {AgentStatus.IDLE, AgentStatus.PAUSED}:
                resumed_status = AgentStatus.RESPONDING if self._current_task and not self._current_task.done() else AgentStatus.IDLE
            await self._set_agent_status(self._active_sid, resumed_status)

    def clear_memory(self) -> None:
        """Limpia el historial de la sesión actual."""
        self._memory.clear()
        logger.info("Memoria limpiada")

    async def new_session(self) -> str:
        """Inicia una nueva sesión de conversación."""
        # Detener cualquier sesión de voz activa para no arrastrar handles/estado del chat anterior
        if hasattr(self, "_realtime_voice") and self._realtime_voice and self._realtime_voice.is_active:
            try:
                await self._realtime_voice.stop_session()
            except Exception as exc:
                logger.warning(f"Error deteniendo realtime voice en new_session: {exc}")
        if hasattr(self, "_simulated_realtime") and self._simulated_realtime and self._simulated_realtime.is_active:
            try:
                await self._simulated_realtime.stop_session()
            except Exception as exc:
                logger.warning(f"Error deteniendo simulated realtime en new_session: {exc}")
        self._memory = Memory()
        await self._memory.initialize()
        if self._planner and hasattr(self._planner, "reset_runtime_contexts"):
            self._planner.reset_runtime_contexts()
        self._base_system_prompt = _load_system_prompt()
        self._memory.set_session_mode(self._current_mode)
        self._apply_system_prompt()
        logger.info(f"Nueva sesión: {self._memory.session_id}")
        return self._memory.session_id

    async def load_session(self, session_id: str) -> None:
        """Carga una sesión anterior."""
        await self._memory.load_session(session_id)
        if self._planner and hasattr(self._planner, "reset_runtime_contexts"):
            self._planner.reset_runtime_contexts()
        self._current_mode = self._memory.session_mode or DEFAULT_MODE_KEY
        self._apply_system_prompt()
        logger.info(f"Sesión cargada: {session_id}")

    def get_status(self) -> dict:
        """Retorna el estado actual del agente."""
        return {
            "running": self._running,
            "paused": self._paused,
            "session_id": self._memory.session_id,
            "message_count": self._memory.message_count,
            "model": self._router.get_current_model() if self._router else None,
            "provider": self._router.get_current_provider_name() if self._router else None,
            "vision_available": self._vision._initialized if self._vision else False,
            "vision_health": self._vision.get_health() if self._vision and hasattr(self._vision, "get_health") else None,
            "automation_enabled": self._automation.is_enabled if self._automation else False,
            "adb_connected": self._adb.is_connected if self._adb else False,
            "tts_engine": self._voice.tts_engine_name if self._voice else "none",
            "stt_available": self._voice.stt_available if self._voice else False,
            "mode": self._current_mode,
            "mode_name": get_mode(self._current_mode).name,
            "autonomy_level": config.get("agent", "autonomy_level", default="supervisado"),
            "pending_approval": self._pending_approval is not None,
            "subagents_active": self._subagents.active_count(),
            "terminals_active": self._terminals.active_count(),
        }

    # ── RT Tool Result Sanitization ─────────────────────

    @staticmethod
    def _sanitize_tool_result(fn_name: str, raw_data, result: dict) -> str:
        """Genera un resumen compacto del resultado de una tool para enviar a Gemini RT.
        Evita enviar datos binarios (base64) u objetos enormes por el WebSocket."""
        # Para screenshot: enviar metadata + descripción visual para que Gemini describa la pantalla
        if fn_name == "screenshot" and isinstance(raw_data, dict):
            dims = raw_data.get("screen_dimensions", {})
            has_image = bool(raw_data.get("image_base64"))
            n_elements = raw_data.get("elements_count", raw_data.get("ui_elements_count", 0))
            msg = result.get("message", "Captura tomada")
            parts = [f"{msg} | dimensiones={dims.get('physical_w', '?')}x{dims.get('physical_h', '?')}"]
            if n_elements:
                parts.append(f"elementos_ui={n_elements}")
            if has_image:
                parts.append("imagen_enviada_como_frame=true")
            # Incluir resumen de UI para que Gemini pueda describir la pantalla
            ui_summary = str(raw_data.get("ui_elements_summary", "") or "").strip()
            if ui_summary:
                parts.append(f"resumen_ui: {ui_summary[:800]}")
            # Incluir etiquetas de elementos detectados CON coordenadas para click preciso
            ui_elements = raw_data.get("ui_elements", [])
            if isinstance(ui_elements, list) and ui_elements:
                labels = []
                for el in ui_elements[:30]:
                    if isinstance(el, dict):
                        label = str(el.get("label", el.get("content", el.get("text", ""))) or "").strip()
                        el_type = str(el.get("type", "") or "").strip()
                        # Extraer coordenadas del centro para click
                        cx = el.get("center_x") or el.get("cx")
                        cy = el.get("center_y") or el.get("cy")
                        if cx is None or cy is None:
                            center = el.get("center")
                            if isinstance(center, (list, tuple)) and len(center) >= 2:
                                cx, cy = int(center[0]), int(center[1])
                            else:
                                bbox = el.get("bbox")
                                if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                                    cx = int((bbox[0] + bbox[2]) / 2)
                                    cy = int((bbox[1] + bbox[3]) / 2)
                        if label:
                            coord_str = f"@({cx},{cy})" if cx is not None and cy is not None else ""
                            prefix = f"{el_type}:" if el_type else ""
                            labels.append(f"{prefix}{label}{coord_str}")
                if labels:
                    parts.append(f"elementos_clickables: {', '.join(labels)}")
            parts.append("INSTRUCCIÓN: analiza el frame de imagen recibido para verificar coordenadas antes de hacer click")
            # Incluir texto OCR si está disponible
            ocr_text = str(raw_data.get("text", "") or "").strip()
            if ocr_text:
                parts.append(f"texto_pantalla: {ocr_text[:600]}")
            summary = " | ".join(parts)
            if len(summary) > 4000:
                summary = summary[:4000] + "...[truncado]"
            return summary
        # Para generación multimedia: enviar solo metadata, sin base64/binarios
        if fn_name in ("generate_image", "generate_video", "generate_music") and isinstance(raw_data, dict):
            files = raw_data.get("files", [])
            clean_files = [
                {k: v for k, v in f.items() if k not in ("base64", "data", "bytes", "image_bytes")}
                for f in files if isinstance(f, dict)
            ] if isinstance(files, list) else []
            clean = {
                "success": raw_data.get("success", len(clean_files) > 0),
                "message": raw_data.get("message", ""),
                "model": raw_data.get("model", ""),
                "count": len(clean_files),
                "files": clean_files,
            }
            lyrics = raw_data.get("lyrics")
            if lyrics:
                clean["lyrics"] = lyrics[:500]
            return str(clean)
        # Para otros tools con dicts
        if isinstance(raw_data, dict):
            # Filtrar campos grandes (base64, raw bytes, etc.)
            clean = {}
            for k, v in raw_data.items():
                if isinstance(v, str) and len(v) > 500:
                    clean[k] = v[:100] + "...[truncado]"
                else:
                    clean[k] = v
            return str(clean)
        # Para datos simples
        raw_str = str(raw_data)
        if len(raw_str) > 1000:
            return raw_str[:1000] + "...[truncado]"
        return raw_str

    # ── Phase 2: Vision helpers ───────────────────────────

    async def take_screenshot(self, sid: str) -> dict | None:
        """Captura y envía screenshot al frontend."""
        if not self._vision:
            return None
        result = await self._vision.analyze_screen()
        if result.get("image_base64"):
            await emit_screenshot(sid, result["image_base64"])
        return result

    @property
    def vision(self):
        return self._vision

    @property
    def automation(self):
        return self._automation

    # ── Phase 3: Voice helpers ────────────────────────────

    async def transcribe_audio(self, audio_bytes: bytes) -> str:
        """Transcribe audio del usuario a texto."""
        if not self._voice:
            return ""
        return await self._voice.transcribe(audio_bytes)

    async def start_realtime_voice(self, sid: str, provider: str = "openai", voice: str = "", mode: str = "native") -> bool:
        """Inicia sesión de voz en tiempo real (nativa o simulada)."""

        # ── Modo simulado: STT → LLM → TTS ───────────────
        if mode == "simulated":
            if not self._simulated_realtime:
                logger.error("SimulatedRealtimeVoice no inicializado")
                return False
            if not self._voice:
                logger.error("VoiceEngine no disponible para modo simulado")
                return False
            if not self._router:
                logger.error("ModelRouter no disponible para modo simulado")
                return False

            sim_text_buffer: list[str] = []

            async def sim_on_audio(audio_bytes: bytes):
                import base64
                b64 = base64.b64encode(audio_bytes).decode("utf-8")
                await sio.emit("agent:audio", {"audio": b64, "format": "pcm16", "stream": True}, to=sid)

            async def sim_on_text(text: str):
                sim_text_buffer.append(text)
                await emit_message_chunk(sid, text)

            async def sim_on_user_text(text: str):
                await emit_message_done(sid)
                await sio.emit("agent:realtime_user_text", {"text": text}, to=sid)

            async def sim_on_turn_complete():
                await emit_message_done(sid)
                sim_text_buffer.clear()

            # Pasar el system prompt real del agente (con modo aplicado) + MCP context + autonomía + prompt de voz
            agent_prompt = build_mode_system_prompt(self._base_system_prompt, self._current_mode)

            autonomy = config.get("agent", "autonomy_level", default="supervisado")
            agent_prompt = agent_prompt + f"\n\n[AUTONOMÍA ACTUAL: {autonomy}]"

            mcp_context = self._get_mcp_tools_context()
            if mcp_context:
                agent_prompt = agent_prompt + "\n\n" + mcp_context
                logger.info(f"SimulatedRT: MCP context inyectado en prompt ({len(mcp_context)} chars)")
            else:
                logger.warning("SimulatedRT: NO hay MCP context disponible para inyectar en prompt de voz")

            voice_prompt, _ = get_prompt_text("voice_realtime")

            return await self._simulated_realtime.start_session(
                voice_engine=self._voice,
                model_router=self._router,
                memory=self._memory,
                system_prompt=agent_prompt,
                voice_prompt=voice_prompt,
                on_audio=sim_on_audio,
                on_text=sim_on_text,
                on_user_text=sim_on_user_text,
                on_turn_complete=sim_on_turn_complete,
                planner=self._planner,
                sio=sio,
                sid=sid,
            )

        # ── Modo nativo: WebSocket directo a proveedor RT ─
        if not self._realtime_voice:
            return False

        # ── Inyectar contexto MCP al RT si el modo es "preloaded" ──
        integration_mode = config.get("mcp", "integration_mode", default="preloaded")
        if integration_mode == "preloaded":
            mcp_context = self._get_mcp_tools_context()
            if mcp_context:
                self._realtime_voice._mcp_context = mcp_context
                logger.info(f"RT: contexto MCP inyectado ({len(mcp_context)} chars, modo=preloaded)")
            else:
                self._realtime_voice._mcp_context = ""
                logger.debug("RT: no hay contexto MCP disponible (sin servidores activos o MCP deshabilitado)")
        else:
            self._realtime_voice._mcp_context = ""
            logger.info(f"RT: modo MCP '{integration_mode}' — sin inyección de contexto")

        # Buffer para acumular texto del agente por turno (para persistir)
        rt_agent_text_buffer: list[str] = []
        # Dedup: track last tool call to skip identical consecutive calls
        _last_rt_tool: dict = {"name": None, "args_key": None, "ts": 0.0}
        # Stuck-loop detection: track repeated click coordinates
        _click_repeat_tracker: dict = {"coords": None, "count": 0}

        async def on_error(error_msg: str):
            """Errores fatales de la sesión RT (quota, auth, modelo inválido)."""
            raw = error_msg.lower()
            if "quota" in raw or "exceeded" in raw or "billing" in raw:
                friendly = (
                    "⚠️ Cuota de Google Gemini Live agotada. "
                    "Revisa tu plan y facturación en https://aistudio.google.com. "
                    "También puedes usar el modelo gemini-2.5-flash-native-audio-preview-12-2025 "
                    "si tienes acceso, o cambiar a modo de voz simulado."
                )
            elif "permission" in raw or "unauthorized" in raw or "api key" in raw:
                friendly = (
                    "⚠️ Error de autenticación con Google Live API. "
                    "Verifica tu API key de Google en Configuración."
                )
            elif "not found" in raw or "does not exist" in raw or "invalid_argument" in raw:
                friendly = (
                    "⚠️ El modelo de voz en tiempo real no está disponible. "
                    "Intenta con gemini-2.5-flash-native-audio-preview-12-2025 en Configuración."
                )
            else:
                friendly = f"⚠️ Error en la sesión de voz en tiempo real: {error_msg}"
            logger.warning(f"RT fatal error notificado al frontend: {error_msg}")
            await emit_message(sid, friendly, "error", done=True)
            await sio.emit("agent:status", {"status": "realtime_stopped"}, to=sid)

        async def on_audio(audio_bytes: bytes):
            import base64
            b64 = base64.b64encode(audio_bytes).decode("utf-8")
            await sio.emit("agent:audio", {"audio": b64, "format": "pcm16", "stream": True}, to=sid)

        async def on_text(text: str):
            rt_agent_text_buffer.append(text)
            logger.debug(f"RT on_text: emitiendo chunk al frontend ({len(text)} chars): {text[:80]!r}")
            await emit_message_chunk(sid, text)

        async def on_user_text(text: str):
            """Muestra la transcripción del habla del usuario en el chat y la persiste."""
            # Cerrar burbuja de streaming del agente si está abierta, para mostrar texto del usuario en orden correcto
            # Nota: si hubo barge-in (interrupted=True sin on_turn_complete), la burbuja puede tener texto parcial —
            # se cierra aquí con lo que haya acumulado hasta ahora
            if rt_agent_text_buffer:
                await emit_message_done(sid)
            await sio.emit(
                "agent:realtime_user_text",
                {"text": text},
                to=sid,
            )
            # Persistir mensaje del usuario en el historial
            try:
                self._memory.add_user_message(text)
                await self._memory.persist_message("user", text)
            except Exception as exc:
                logger.warning(f"RT: no se pudo persistir mensaje de usuario: {exc}")

        async def on_turn_complete():
            """Señala fin del turno del agente para cerrar la burbuja de streaming."""
            await emit_message_done(sid)
            # Persistir respuesta acumulada del agente
            full_response = "".join(rt_agent_text_buffer).strip()
            rt_agent_text_buffer.clear()
            logger.info(f"RT turn complete: respuesta acumulada ({len(full_response)} chars): {full_response[:120]!r}")
            if full_response:
                try:
                    self._memory.add_assistant_message(full_response)
                    await self._memory.persist_message("assistant", full_response)
                except Exception as exc:
                    logger.warning(f"RT: no se pudo persistir respuesta del agente: {exc}")

        async def on_tool_call(tool_call: dict):
            """Ejecuta herramientas agénticas invocadas por el modelo RT."""
            import time as _time
            function_responses = []
            _screenshot_responses: list[str] = []
            for fc in tool_call.get("functionCalls", []):
                fn_name = fc.get("name", "")
                fn_args = fc.get("args", {})
                fn_id = fc.get("id", "")

                # Dedup: skip if model calls same tool with same args < 2s ago
                # Prevents double-type, double-click, double-enter issues
                _now = _time.monotonic()
                _args_key = str(sorted(fn_args.items()) if isinstance(fn_args, dict) else fn_args)
                if (fn_name == _last_rt_tool["name"]
                        and _args_key == _last_rt_tool["args_key"]
                        and _now - _last_rt_tool["ts"] < 2.0):
                    logger.warning(f"RT dedup: skip duplicate tool call {fn_name}({fn_args})")
                    # Still need to return a response so model doesn't hang
                    function_responses.append({"name": fn_name, "id": fn_id, "response": {"result": "skipped_duplicate"}})
                    continue
                _last_rt_tool["name"] = fn_name
                _last_rt_tool["args_key"] = _args_key
                _last_rt_tool["ts"] = _now

                logger.info(f"RT tool call: {fn_name}({fn_args})")

                # Defense-in-depth: el coordinador (voz) no tiene acciones de escritorio nativas.
                # Toda interacción de UI debe ir por delegate_computer_use.
                if fn_name in {"click", "double_click", "right_click", "type", "focus_type",
                               "press", "key", "hotkey", "scroll", "move", "drag"}:
                    logger.warning(f"RT bloqueado tool desktop nativo: {fn_name}")
                    function_responses.append({"name": fn_name, "id": fn_id, "response": {"error": (
                        "Acción de escritorio directa no disponible para el coordinador. "
                        "Usa delegate_computer_use(task=\"...\", monitor=N) para que el sub-agente realice la interacción."
                    )}})
                    continue

                # Stuck-loop detection: if clicking same coords 3+ times, override with error
                if fn_name in ("click", "double_click", "right_click"):
                    _coords = (fn_args.get("x"), fn_args.get("y"))
                    if _coords == _click_repeat_tracker["coords"]:
                        _click_repeat_tracker["count"] += 1
                    else:
                        _click_repeat_tracker["coords"] = _coords
                        _click_repeat_tracker["count"] = 1

                    if _click_repeat_tracker["count"] >= 3:
                        logger.warning(f"RT stuck-loop: click at {_coords} repeated {_click_repeat_tracker['count']} times")
                        function_responses.append({
                            "name": fn_name, "id": fn_id,
                            "response": {
                                "error": (
                                    f"STUCK: Has hecho click en ({_coords[0]}, {_coords[1]}) "
                                    f"{_click_repeat_tracker['count']} veces sin resultado. "
                                    "El click NO está funcionando en esta coordenada. "
                                    "DETENTE. Toma un screenshot nuevo, analiza la imagen, "
                                    "y usa coordenadas DIFERENTES basadas en lo que VES en la pantalla actual. "
                                    "Si el elemento que buscas no es clickeable, prueba otra estrategia."
                                ),
                            },
                        })
                        continue
                elif fn_name != "screenshot":
                    _click_repeat_tracker["coords"] = None
                    _click_repeat_tracker["count"] = 0

                # Generar ID único para esta acción (para vincular card con resultado)
                import uuid as _uuid
                action_id = _uuid.uuid4().hex[:8]

                # Notificar al frontend que se ejecuta una acción (con actionId)
                await sio.emit("agent:action", {"type": fn_name, "params": fn_args, "actionId": action_id}, to=sid)

                result_data = {"error": f"Tool '{fn_name}' no disponible"}
                try:
                    if fn_name == "set_emotion":
                        # Expresión facial/corporal del avatar (VRM/sprite). El frontend
                        # enruta agent:emotion → skin.setEmotion. Sin computer use.
                        _emo = str(fn_args.get("emotion", "")).strip().lower()
                        _valid_emos = {"happy", "sad", "angry", "surprised", "relaxed", "neutral"}
                        if _emo in _valid_emos:
                            await emit_emotion(sid, _emo)
                            result_data = {"result": f"Expresión '{_emo}' aplicada a tu avatar."}
                        else:
                            result_data = {"error": (
                                f"Emoción inválida: '{_emo}'. Válidas: {', '.join(sorted(_valid_emos))}."
                            )}
                    elif fn_name == "delegate_computer_use":
                        cu_task = str(fn_args.get("task", "")).strip()
                        cu_monitor = int(fn_args.get("monitor", 0) or 0)
                        if not cu_task:
                            result_data = {"error": "Falta 'task' para delegate_computer_use"}
                        else:
                            cu_result = await self._handle_computer_use_delegation(sid, cu_task, cu_monitor)
                            if cu_result:
                                recent = ", ".join(cu_result.action_history[-6:]) if cu_result.action_history else "ninguna"
                                result_data = {"result": (
                                    f"Computer use [{cu_result.status}]: {cu_result.summary or cu_result.status}. "
                                    f"Acciones: {recent}."
                                    + (f" Error: {cu_result.error}" if cu_result.error else "")
                                )}
                                # Adjuntar frame de verificación para que el modelo live lo analice
                                try:
                                    _verify_mon = cu_monitor or int(config.get("vision", "target_monitor", default=0))
                                    _vs = await self._vision.analyze_screen(mode="computer_use", monitor=_verify_mon)
                                    _vb = _vs.get("image_base64")
                                    if _vb:
                                        result_data["_screenshot_b64"] = _vb
                                except Exception as _exc:
                                    logger.debug(f"RT verificación post-delegación falló: {_exc}")
                            else:
                                result_data = {"error": "Sub-agente de computer use no disponible o falló."}
                    elif fn_name == "mcp_call_tool":
                        # ── MCP tool execution via runtime ──
                        mcp_server_id = str(fn_args.get("server_id", "")).strip()
                        mcp_tool_name = str(fn_args.get("tool", "")).strip()
                        mcp_arguments = fn_args.get("arguments") or {}
                        if not mcp_server_id or not mcp_tool_name:
                            result_data = {"error": "Faltan 'server_id' y/o 'tool' para mcp_call_tool."}
                        else:
                            try:
                                mcp_runtime = getattr(self._planner, '_mcp_runtime', None) if self._planner else None
                                if not mcp_runtime:
                                    result_data = {"error": "MCP runtime no disponible. Verifica que MCP esté habilitado en Configuración > Integraciones."}
                                else:
                                    import asyncio as _aio
                                    mcp_result = await _aio.to_thread(
                                        mcp_runtime.call_tool,
                                        server_id=mcp_server_id,
                                        tool_name=mcp_tool_name,
                                        arguments=mcp_arguments if isinstance(mcp_arguments, dict) else {},
                                    )
                                    if mcp_result.get("success"):
                                        # Extraer contenido legible del resultado MCP
                                        content_parts = mcp_result.get("content", [])
                                        text_parts = []
                                        for part in content_parts:
                                            if isinstance(part, dict) and part.get("type") == "text":
                                                text_parts.append(part.get("text", ""))
                                            elif isinstance(part, str):
                                                text_parts.append(part)
                                        mcp_text = "\n".join(text_parts) if text_parts else json.dumps(mcp_result.get("raw_result", {}), ensure_ascii=False)
                                        # Truncar si es demasiado largo para el contexto del modelo
                                        if len(mcp_text) > 4000:
                                            mcp_text = mcp_text[:4000] + "\n... [truncado]"
                                        result_data = {"result": f"MCP {mcp_server_id}/{mcp_tool_name}: {mcp_text}"}
                                    else:
                                        mcp_err = mcp_result.get('error') or 'error desconocido'
                                        result_data = {"error": f"MCP tool falló: {mcp_err}"}
                            except Exception as mcp_exc:
                                logger.error(f"RT mcp_call_tool error: {mcp_exc}")
                                result_data = {"error": f"Error ejecutando MCP tool: {mcp_exc}"}
                    elif self._planner:
                        from backend.core.planner import Action
                        action = Action(type=fn_name, params=fn_args)
                        result = await self._planner._execute_single(action)
                        if result.get("success"):
                            raw_data = result.get("data", "OK")
                            # Sanitizar resultado para tool response (no enviar datos binarios/enormes a Gemini)
                            result_summary = self._sanitize_tool_result(fn_name, raw_data, result)
                            result_data = {"result": result_summary}
                            # Si hay screenshot, emitir imagen al frontend y prepararla para tool response
                            if fn_name == "screenshot" and isinstance(raw_data, dict) and raw_data.get("image_base64"):
                                await emit_screenshot(sid, raw_data["image_base64"])
                                # Incluir la imagen DENTRO del tool response (no como frame separado)
                                # para que el modelo la analice como resultado directo del screenshot
                                _img_b64 = raw_data["image_base64"]
                                if _img_b64.startswith("data:"):
                                    _img_b64 = _img_b64.split(",", 1)[-1]
                                result_data["_screenshot_b64"] = _img_b64
                            # Emitir archivos multimedia generados al frontend
                            if fn_name in ("generate_image", "generate_video", "generate_music") and isinstance(raw_data, dict):
                                for gen_file in (raw_data.get("files") or []):
                                    fname = gen_file.get("filename", "")
                                    if fn_name == "generate_image" and gen_file.get("base64"):
                                        await emit_screenshot(sid, gen_file["base64"], caption="generated_image")
                                    if fname:
                                        media_type = "image" if fn_name == "generate_image" else "video" if fn_name == "generate_video" else "audio"
                                        await emit_media(sid, media_type, fname, f"/api/media/{fname}")
                        else:
                            result_data = {"error": str(result.get("message") or result.get("error", "fallo"))}
                except Exception as exc:
                    logger.error(f"RT tool exec error ({fn_name}): {exc}")
                    result_data = {"error": str(exc)}

                # Notificar al frontend el resultado de la acción
                is_success = "result" in result_data
                result_text = result_data.get("result") or result_data.get("error", "")
                logger.info(f"RT tool result ({fn_name}): success={is_success}, result_len={len(result_text)}, preview={result_text[:100]!r}")
                await sio.emit("agent:action_result", {
                    "actionId": action_id,
                    "success": is_success,
                    "result": result_text,
                }, to=sid)

                # Build function response — include screenshot image inline if available
                _screenshot_b64 = result_data.pop("_screenshot_b64", None)
                _fr = {
                    "name": fn_name,
                    "id": fn_id,
                    "response": result_data,
                }
                function_responses.append(_fr)

                # Send screenshot as a SEPARATE inline image in the tool response
                # so the model sees it as the direct result of the screenshot tool
                if _screenshot_b64:
                    _screenshot_responses.append(_screenshot_b64)

                # Persistir tool call y resultado en la memoria
                try:
                    tool_meta = {
                        "tool_name": fn_name,
                        "params": fn_args,
                        "success": is_success,
                        "result_preview": str(result_text)[:200],
                    }
                    await self._memory.persist_message(
                        "assistant",
                        f"[tool_call] {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:200]})",
                        message_type="tool_call",
                        metadata=tool_meta,
                    )
                except Exception as exc:
                    logger.debug(f"RT: no se pudo persistir tool call: {exc}")

            # Enviar respuestas de herramientas de vuelta al modelo
            await self._realtime_voice.send_tool_response(function_responses)

            # Send screenshot images AFTER tool response as video frames
            # so the model can see the screen state for its next decision
            for _ss_b64 in _screenshot_responses:
                await self._realtime_voice.send_image(_ss_b64)

        return await self._realtime_voice.start_session(
            provider, on_audio, on_text, on_user_text, on_tool_call, voice=voice,
            on_turn_complete=on_turn_complete,
            conversation_history=self._memory.messages if self._memory.messages else None,
            on_error=on_error,
        )

    async def stop_realtime_voice(self) -> None:
        """Detiene la sesión de voz en tiempo real (nativa o simulada)."""
        # Cancelar operaciones de media en curso (video polling, etc.)
        try:
            from backend.providers.google_media import cancel_media_generation
            cancel_media_generation()
        except Exception:
            pass
        if self._simulated_realtime and self._simulated_realtime.is_active:
            await self._simulated_realtime.stop_session()
        if self._realtime_voice:
            await self._realtime_voice.stop_session()

    async def process_message_live(self, sid: str, text: str) -> None:
        """
        Procesa un mensaje de texto usando la Live API.
        Según la documentación oficial de Google:
          - Entrada: texto vía realtimeInput.text
          - Salida: audio PCM (agent:audio) + transcripción (mensajes de chat)
        Ref: https://ai.google.dev/gemini-api/docs/live-guide#sending-text-message
        """
        if not self._realtime_voice:
            return

        # Auto-iniciar sesión Live si no está activa
        if not self._realtime_voice.is_active:
            logger.info(f"Live API: auto-iniciando sesión para mensaje de texto: {text[:50]!r}")
            success = await self.start_realtime_voice(sid, "google")
            if not success:
                await emit_message_chunk(
                    sid,
                    "⚠️ No se pudo iniciar la sesión con el modelo Live. "
                    "Verifica tu API key de Google en Settings.",
                )
                await emit_message_done(sid)
                return
            # Notificar al frontend que la sesión Live está activa
            await sio.emit(
                "agent:status",
                {"status": "realtime_active", "provider": "google", "mode": "native"},
                to=sid,
            )

        # Enviar texto — send_text() espera internamente a que _google_ready sea True
        await self._realtime_voice.send_text(text)
        # La respuesta llega de forma asíncrona vía callbacks:
        #   audio chunk  → on_audio → sio.emit("agent:audio")
        #   transcripción → on_text → emit_message_chunk / emit_message_done

    async def send_realtime_audio(self, audio_chunk: bytes) -> None:
        """Reenvía un chunk de audio del micrófono al proveedor RT o simulado."""
        if self._simulated_realtime and self._simulated_realtime.is_active:
            await self._simulated_realtime.send_audio(audio_chunk)
        elif self._realtime_voice and self._realtime_voice.is_active:
            await self._realtime_voice.send_audio(audio_chunk)

    async def start_screen_stream(self) -> bool:
        """Inicia screen streaming hacia la sesión RT activa."""
        if self._realtime_voice and self._realtime_voice.is_active:
            return await self._realtime_voice.start_screen_stream()
        return False

    async def stop_screen_stream(self) -> None:
        """Detiene screen streaming."""
        if self._realtime_voice:
            await self._realtime_voice.stop_screen_stream()

    @property
    def is_screen_streaming(self) -> bool:
        if self._realtime_voice:
            return self._realtime_voice.is_screen_streaming
        return False

    @property
    def voice(self):
        return self._voice
