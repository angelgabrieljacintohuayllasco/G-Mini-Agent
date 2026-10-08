"""
G-Mini Agent — API remota v1 (contrato en docs/protocol/remote-api-v1.md).

La usan la CLI, la extensión del navegador, otras PCs y los dispositivos
compañeros (ESP32, Raspberry Pi). Autenticación por token (local_auth): el
middleware HTTP deja el AuthInfo en request.state.auth; el WebSocket valida
el token en el handshake. Cada ruta exige un scope.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse
from loguru import logger

from backend.api import client_sinks
from backend.config import config
from backend.security import local_auth

PROTOCOL_VERSION = 1
router = APIRouter(prefix="/v1")
_background: set[asyncio.Task] = set()

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})


def is_headless() -> bool:
    return os.environ.get("GMINI_HEADLESS") == "1"


def _auth(request: Request) -> local_auth.AuthInfo:
    info = getattr(request.state, "auth", None)
    if info is None:  # require_token desactivado (solo desarrollo)
        return local_auth.AuthInfo(kind="session", scopes=local_auth.ALL_SCOPES)
    return info


def _require(request: Request, scope: str) -> local_auth.AuthInfo:
    info = _auth(request)
    if not info.has_scope(scope):
        raise ApiError(403, "missing_scope", f"El token no tiene el permiso '{scope}'.")
    return info


def _agent():
    from backend.api.websocket_handler import _agent_core

    if _agent_core is None:
        raise ApiError(503, "not_ready", "El agente todavía está iniciando.")
    return _agent_core


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise ApiError(400, "bad_request", "El cuerpo debe ser JSON.") from None
    if not isinstance(body, dict):
        raise ApiError(400, "bad_request", "El cuerpo debe ser un objeto JSON.")
    return body


# ── Ejecución de un turno de chat para clientes v1 ────────────────────────

_STATUS_MAP = {
    "idle": "idle", "paused": "idle", "error": "idle", "thinking": "thinking", "responding": "speaking",
    "executing": "acting", "acting": "acting", "calling": "acting", "listening": "listening",
}


_ACTION_MARKUP = re.compile(r"\[ACTION:[^\]]*\]")
_ACTION_PREFIX = "[ACTION:"


@dataclass
class ChatResult:
    """Respuesta de un turno. Cada llamada al modelo es un segmento; `reply` es el
    último (la conclusión) y nunca incluye el marcado [ACTION:...]."""

    session_id: str = ""
    actions: list[dict[str, Any]] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    approval_pending: bool = False
    error: dict[str, str] | None = None
    segments: list[str] = field(default_factory=list)
    _raw: str = ""
    _emitted: int = 0

    def _visible(self) -> str:
        text = _ACTION_MARKUP.sub("", self._raw)
        start = text.rfind("[")
        if start != -1 and "]" not in text[start:]:
            tail = text[start:]
            if tail.startswith(_ACTION_PREFIX) or _ACTION_PREFIX.startswith(tail):
                text = text[:start]  # una acción a medio llegar no se muestra
        return text

    def feed(self, chunk: str) -> str:
        """Agrega texto del stream y devuelve solo lo nuevo visible."""
        self._raw += chunk
        visible = self._visible()
        delta = visible[self._emitted:]
        self._emitted = len(visible)
        return delta

    def end_segment(self) -> str:
        """Cierra el mensaje en curso; devuelve el resto visible que faltaba enviar."""
        text = _ACTION_MARKUP.sub("", self._raw)
        rest = text[self._emitted:]
        if text.strip():
            self.segments.append(text.strip())
        self._raw, self._emitted = "", 0
        return rest

    @property
    def reply(self) -> str:
        current = _ACTION_MARKUP.sub("", self._raw).strip()
        return current or (self.segments[-1] if self.segments else "")

    def to_dict(self) -> dict[str, Any]:
        data = {"session_id": self.session_id, "reply": self.reply.strip(), "actions": self.actions}
        if self.notices:
            data["notices"] = self.notices
        if self.approval_pending:
            data["approval_pending"] = True
        if self.error:
            data["error"] = self.error
        return data


def translate_event(event: str, data: Any, result: ChatResult) -> list[dict[str, Any]]:
    """Evento interno del agente (Socket.IO) → eventos del protocolo v1."""
    data = data if isinstance(data, dict) else {}
    if event == "agent:message":
        kind, text = str(data.get("type") or "text"), str(data.get("text") or "")
        if kind == "text":
            visible = result.feed(text) if text else ""
            if data.get("done"):
                visible += result.end_segment()
            return [{"type": "chunk", "text": visible}] if visible else []
        if kind == "error":
            result.error = {"code": "agent_error", "message": text}
            return [{"type": "error", "code": "agent_error", "message": text}]
        if text:
            result.notices.append(text)
            return [{"type": "notice", "kind": kind, "text": text}]
        return []
    if event in ("agent:status", "agent:emotion"):
        state = client_sinks.agent_state()
        return [{"type": "state", "status": _STATUS_MAP.get(state["status"], "thinking"), "emotion": state["emotion"]}]
    if event == "agent:action":
        entry = {"action": data.get("type"), "params": data.get("params") or {}, "id": data.get("actionId")}
        result.actions.append({"action": entry["action"], "params": entry["params"]})
        return [{"type": "action", **entry}]
    if event == "agent:action_result":
        name, success, message = data.get("type"), bool(data.get("success")), str(data.get("result") or "")
        for item in reversed(result.actions):
            if item.get("action") == name and "success" not in item:
                item.update({"success": success, "message": message})
                break
        return [{"type": "action_result", "action": name, "success": success, "message": message,
                 "id": data.get("actionId")}]
    if event == "agent:approval":
        if data.get("pending"):
            result.approval_pending = True
        return [{"type": "approval", "pending": bool(data.get("pending")), "summary": data.get("summary", ""),
                 "kind": data.get("kind", "approval")}]
    return []


async def run_chat(
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    *,
    on_event: EventCallback | None = None,
    sid: str | None = None,
    wait_if_busy: float = 0.0,
) -> ChatResult:
    """Un turno completo del agente con un sid virtual; devuelve la respuesta reunida."""
    agent = _agent()
    deadline = time.monotonic() + wait_if_busy
    while agent._session_context_lock.locked():
        if time.monotonic() >= deadline:
            raise ApiError(409, "busy", "El agente está ocupado con otra conversación.")
        await asyncio.sleep(1.0)

    sid = sid or f"{client_sinks.SID_PREFIX}{uuid.uuid4().hex[:12]}"
    queue: asyncio.Queue = asyncio.Queue()
    client_sinks.register(sid, lambda event, data: queue.put_nowait((event, data)))
    result = ChatResult()
    task = asyncio.create_task(agent.process_message(sid, text, attachments or None))

    async def emit_all(event: str, data: Any) -> None:
        for item in translate_event(event, data, result):
            if on_event is not None:
                await on_event(item)

    try:
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                await emit_all(*getter.result())
                continue
            getter.cancel()
            while not queue.empty():
                await emit_all(*queue.get_nowait())
            break
        if task.exception() is not None:
            result.error = {"code": "agent_error", "message": str(task.exception())}
    finally:
        client_sinks.unregister(sid)
    result.session_id = agent.memory.session_id
    return result


def _attachments(raw: Any) -> list[dict[str, Any]]:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and item.get("data_base64"):
            out.append({
                "kind": "file",
                "file_name": str(item.get("name") or "adjunto"),
                "mime_type": str(item.get("mime_type") or "application/octet-stream"),
                "data": str(item["data_base64"]),
            })
    return out


def _sse(item: dict[str, Any]) -> bytes:
    payload = {k: v for k, v in item.items() if k != "type"}
    return f"event: {item['type']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


# ── Rutas REST ─────────────────────────────────────────────────────────────

def _agent_identity() -> dict[str, Any]:
    from backend.core import identity

    return {
        "name": identity.agent_name(),
        "language": identity.language(),
        "voice": str(config.get("voice", "edge_voice", default="") or ""),
    }


@router.get("/health")
async def health():
    from backend.core import identity

    return {
        "ok": True,
        "protocol": PROTOCOL_VERSION,
        "version": str(config.get("app", "version", default="0.1.0")),
        "mode": "server" if is_headless() else "desktop",
        "name": identity.agent_name(),
        "requires_auth": local_auth.auth_required(),
    }


@router.get("/me")
async def me(request: Request):
    info = _auth(request)
    return {"kind": info.kind, "device_id": info.device_id, "device_name": info.device_name,
            "scopes": list(info.scopes), "agent": _agent_identity()}


@router.post("/pairing", status_code=201)
async def create_pairing(request: Request):
    _require(request, "admin")
    body = await _json_body(request)
    pairing = local_auth.create_pairing_code(
        label=str(body.get("label") or ""), device_type=str(body.get("device_type") or "custom"),
        scopes=body.get("scopes") if isinstance(body.get("scopes"), list) else None,
    )
    host = request.url.hostname or "127.0.0.1"
    port = request.url.port or int(config.get("server", "port", default=8765) or 8765)
    expires = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(pairing["expires_at"]))
    return {"code": pairing["code"], "expires_at": expires,
            "qr_payload": f"gmini://pair?host={host}&port={port}&code={pairing['code']}"}


@router.post("/pairing/claim")
async def claim_pairing(request: Request):
    body = await _json_body(request)
    try:
        token, device = local_auth.claim_pairing_code(
            str(body.get("code") or ""),
            device_name=str(body.get("device_name") or "")[:80],
            device_type=str(body.get("device_type") or "")[:32],
            platform=str(body.get("platform") or "")[:64],
            client_ip=request.client.host if request.client else "",
        )
    except PermissionError as exc:
        if str(exc) == "rate_limited":
            raise ApiError(429, "rate_limited", "Demasiados intentos; espera un minuto.") from None
        raise ApiError(401, "invalid_code", "Código inválido o vencido.") from None
    # server_name es el equipo (como lo documenta el contrato); agent_name, cómo se llama el agente.
    return {"token": token, "device_id": device.id, "server_name": socket.gethostname() or "G-Mini",
            "agent_name": _agent_identity()["name"], "scopes": device.scopes}


@router.post("/tokens", status_code=201)
async def create_api_token(request: Request):
    """Token para scripts e integraciones; se muestra una sola vez."""
    _require(request, "admin")
    body = await _json_body(request)
    label = " ".join(str(body.get("label") or "").split())[:80]
    if not label:
        raise ApiError(422, "validation_error", "Falta 'label' (para qué es el token).")
    scopes = body.get("scopes") or ["chat", "tasks"]
    if not isinstance(scopes, list) or any(s not in local_auth.ALL_SCOPES for s in scopes):
        raise ApiError(422, "validation_error", f"scopes válidos: {', '.join(local_auth.ALL_SCOPES)}")
    token, record = local_auth.issue_device_token(label, kind="api", device_type="api", scopes=scopes)
    return {"token": token, "id": record.id, "name": record.name, "scopes": record.scopes}


@router.get("/devices")
async def list_devices(request: Request):
    _require(request, "admin")
    return {"items": local_auth.list_devices()}


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: str, request: Request):
    _require(request, "admin")
    if not local_auth.revoke_device(device_id):
        raise ApiError(404, "not_found", "Dispositivo no encontrado.")
    return {"ok": True}


async def _session_exists(session_id: str) -> bool:
    import aiosqlite

    from backend.core import memory as memory_module

    async with aiosqlite.connect(memory_module.DB_PATH) as db:
        async with db.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)) as cursor:
            return await cursor.fetchone() is not None


async def _select_session(session_id: Any) -> None:
    """session_id: vacío = la conversación actual; "new" = una nueva; un id = retomar esa."""
    wanted = str(session_id or "").strip()
    if not wanted:
        return
    agent = _agent()
    if wanted.lower() not in ("new", "nueva") and wanted == agent.memory.session_id:
        return
    if agent._session_context_lock.locked():
        raise ApiError(409, "busy", "El agente está ocupado con otra conversación.")
    if wanted.lower() in ("new", "nueva"):
        await agent.new_session()
        return
    if not await _session_exists(wanted):
        raise ApiError(404, "not_found", "No existe esa conversación.")
    await agent.load_session(wanted)


@router.post("/chat")
async def chat(request: Request):
    _require(request, "chat")
    body = await _json_body(request)
    message = str(body.get("message") or "").strip()
    if not message:
        raise ApiError(422, "validation_error", "Falta 'message'.")
    attachments = _attachments(body.get("attachments"))
    await _select_session(body.get("session_id"))
    wants_stream = bool(body.get("stream")) or "text/event-stream" in request.headers.get("accept", "")
    if not wants_stream:
        result = await run_chat(message, attachments)
        return result.to_dict()

    agent = _agent()
    if agent._session_context_lock.locked():
        raise ApiError(409, "busy", "El agente está ocupado con otra conversación.")
    queue: asyncio.Queue = asyncio.Queue()

    async def produce() -> None:
        try:
            await queue.put({"type": "start", "session_id": agent.memory.session_id})
            result = await run_chat(message, attachments, on_event=queue.put)
            await queue.put({"type": "done", **result.to_dict()})
        except ApiError as exc:
            await queue.put({"type": "error", "code": exc.code, "message": exc.message})
        except Exception as exc:
            await queue.put({"type": "error", "code": "agent_error", "message": str(exc)})
        finally:
            await queue.put(None)

    async def stream():
        # Si el cliente se desconecta, el turno termina igual (la referencia evita que lo recoja el GC).
        producer = asyncio.create_task(produce())
        _background.add(producer)
        producer.add_done_callback(_background.discard)
        while (item := await queue.get()) is not None:
            yield _sse(item)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _iso_with_zone(value: Any) -> Any:
    """La memoria guarda hora local sin zona; la API la devuelve con su desfase (ISO 8601)."""
    if not value:
        return value
    from datetime import datetime

    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return value
    if moment.tzinfo is None:
        moment = moment.astimezone()  # sin zona = hora local del equipo
    return moment.isoformat(timespec="seconds")


@router.get("/sessions")
async def list_sessions(request: Request, limit: int = Query(default=20, ge=1, le=200)):
    _require(request, "chat")
    sessions = await _agent().memory.list_sessions(limit=limit)
    return {"items": [{"id": s["session_id"], "title": s["title"], "updated_at": _iso_with_zone(s["updated_at"]),
                       "message_count": s["message_count"]} for s in sessions]}


@router.get("/sessions/{session_id}/messages")
async def session_messages(session_id: str, request: Request, limit: int = Query(default=100, ge=1, le=1000)):
    _require(request, "chat")
    items = await _agent().memory.get_session_messages(session_id, limit=limit)
    return {"items": [{**item, "created_at": _iso_with_zone(item.get("created_at"))} for item in items]}


@router.get("/agent/state")
async def agent_state(request: Request):
    info = _auth(request)
    if not (info.has_scope("chat") or info.has_scope("node")):
        raise ApiError(403, "missing_scope", "Se necesita 'chat' o 'node'.")
    state = client_sinks.agent_state()
    agent = _agent()
    return {"status": _STATUS_MAP.get(state["status"], "thinking"), "emotion": state["emotion"],
            "busy": agent._session_context_lock.locked(), "approval_pending": agent._pending_approval is not None}


@router.post("/approvals")
async def resolve_approval(request: Request):
    _require(request, "chat")
    body = await _json_body(request)
    agent = _agent()
    pending = agent._pending_approval
    if not pending:
        raise ApiError(404, "not_found", "No hay acciones esperando aprobación.")
    sid, token = str(pending.get("sid") or ""), str(pending.get("token") or "")
    if body.get("approve"):
        asyncio.create_task(agent.approve_pending(sid, token))
        return {"ok": True, "status": "approved"}
    await agent.reject_pending(sid, token)
    return {"ok": True, "status": "rejected"}


# ── Voz ─────────────────────────────────────────────────────────────────

async def synthesize_for_device(text: str, voice: str | None = None) -> bytes:
    """WAV para dispositivos: el motor activo o, si es del navegador, voces de Edge."""
    from backend.voice import tts_engines

    engine = getattr(_agent(), "voice", None)
    audio = None
    if engine is not None and not engine.tts_is_browser and engine.tts_available:
        audio = await engine.synthesize(text, voice_id=voice)
    if not audio and tts_engines.HAS_EDGE_TTS:
        default = str(config.get("voice", "edge_voice", default="") or "es-PE-CamilaNeural")
        audio = await tts_engines.synthesize_edge(text, voice or default)
    if not audio:
        raise ApiError(503, "provider_unavailable", "No hay un motor de voz disponible en el servidor.")
    return audio


async def _transcribe(audio: bytes, prompt: str | None = None) -> str:
    engine = getattr(_agent(), "voice", None)
    if engine is None:
        raise ApiError(503, "provider_unavailable", "La voz no está inicializada.")
    if not audio:
        raise ApiError(422, "validation_error", "El cuerpo debe traer el audio (WAV).")
    return (await (engine.transcribe(audio, prompt=prompt) if prompt else engine.transcribe(audio))).strip()


@router.post("/voice/wake")
async def voice_wake(request: Request):
    """Clip corto (WAV) -> si empieza con la palabra de activación y qué se pidió después."""
    _require(request, "voice")
    from backend.voice.wake_word import match_wake, wake_phrases

    name = _agent_identity()["name"]
    transcript = await _transcribe(await request.body(), prompt=f"Oye {name}.")
    extra = config.get("voice", "wake_word", "phrases", default=[]) or []
    hit = match_wake(transcript, wake_phrases(name, list(extra)))
    return {"wake": bool(hit), "phrase": hit[0] if hit else "", "command": hit[1] if hit else "",
            "transcript": transcript}


@router.post("/voice/tts")
async def voice_tts(request: Request):
    _require(request, "voice")
    body = await _json_body(request)
    text = " ".join(str(body.get("text") or "").split())[:2000]
    if not text:
        raise ApiError(422, "validation_error", "Falta 'text'.")
    return Response(content=await synthesize_for_device(text, body.get("voice")), media_type="audio/wav")


@router.post("/voice/stt")
async def voice_stt(request: Request):
    _require(request, "voice")
    return {"text": await _transcribe(await request.body())}


@router.post("/voice/turn")
async def voice_turn(request: Request):
    _require(request, "voice")
    _require(request, "chat")
    transcript = await _transcribe(await request.body())
    if not transcript:
        return {"transcript": "", "reply": "", "session_id": _agent().memory.session_id}
    await _select_session(request.query_params.get("session_id"))
    result = await run_chat(transcript)
    reply = result.reply.strip()
    data: dict[str, Any] = {"transcript": transcript, "reply": reply, "session_id": result.session_id,
                            "emotion": client_sinks.agent_state()["emotion"]}
    if reply:
        audio = await synthesize_for_device(reply[:1500])
        data.update({"audio_mime": "audio/wav", "audio_base64": base64.b64encode(audio).decode("ascii")})
    return data


# ── Tareas en segundo plano (trabajador 24/7) ─────────────────────────────

@router.post("/tasks", status_code=202)
async def create_task(request: Request):
    from backend.core import remote_tasks

    _require(request, "tasks")
    body = await _json_body(request)
    try:
        task = await remote_tasks.create_task(
            prompt=str(body.get("prompt") or ""), title=str(body.get("title") or ""),
            schedule=body.get("schedule"), notify=body.get("notify") or [],
        )
    except ValueError as exc:
        raise ApiError(422, "validation_error", str(exc)) from None
    return {"task_id": task["task_id"], "status": task["status"]}


@router.get("/tasks")
async def list_tasks(request: Request, status: str | None = None, limit: int = Query(default=50, ge=1, le=500)):
    from backend.core import remote_tasks

    _require(request, "tasks")
    return {"items": await remote_tasks.list_tasks(status=status, limit=limit)}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    from backend.core import remote_tasks

    _require(request, "tasks")
    task = await remote_tasks.get_task(task_id)
    if task is None:
        raise ApiError(404, "not_found", "Tarea no encontrada.")
    return task


@router.delete("/tasks/{task_id}")
async def cancel_task(task_id: str, request: Request):
    from backend.core import remote_tasks

    _require(request, "tasks")
    if not await remote_tasks.cancel_task(task_id):
        raise ApiError(404, "not_found", "Tarea no encontrada.")
    return {"ok": True, "status": "cancelled"}


# ── WebSocket ─────────────────────────────────────────────────────────────

async def _ws_auth(ws: WebSocket) -> local_auth.AuthInfo | None:
    if not local_auth.host_is_allowed(ws.headers.get("host")) or local_auth.browser_origin_is_untrusted(
        ws.headers.get("origin"), allow_extensions=True
    ):
        return None
    if not local_auth.auth_required():
        return local_auth.AuthInfo(kind="session", scopes=local_auth.ALL_SCOPES)
    return local_auth.verify_token(local_auth.extract_token(ws.headers, ws.query_params))


@router.websocket("/ws")
async def websocket_v1(ws: WebSocket):
    info = await _ws_auth(ws)
    if info is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    sid = f"{client_sinks.SID_PREFIX}ws_{uuid.uuid4().hex[:10]}"
    outbox: asyncio.Queue = asyncio.Queue()
    registered_node = False

    def sink(event: str, data: Any) -> None:
        if event == "node:invoke" and isinstance(data, dict):
            outbox.put_nowait({"type": "node.invoke", "request_id": data.get("request_id"),
                               "surface": data.get("surface"), "params": data.get("params") or {}})
            return
        for item in translate_event(event, data, ChatResult()):
            if item["type"] == "state":
                outbox.put_nowait(item)

    client_sinks.register(sid, sink, observe=True)

    async def writer() -> None:
        while True:
            await ws.send_text(json.dumps(await outbox.get(), ensure_ascii=False))

    writer_task = asyncio.create_task(writer())
    chat_task: asyncio.Task | None = None
    try:
        while True:
            try:
                frame = json.loads(await ws.receive_text())
            except json.JSONDecodeError:
                outbox.put_nowait({"type": "error", "code": "bad_request", "message": "Frame JSON inválido."})
                continue
            kind = str(frame.get("type") or "")
            if kind == "ping":
                outbox.put_nowait({"type": "pong"})
            elif kind == "hello":
                outbox.put_nowait({"type": "ready", "agent_name": _agent_identity()["name"],
                                   "protocol": PROTOCOL_VERSION, "session_id": _agent().memory.session_id})
            elif kind == "chat":
                if not info.has_scope("chat"):
                    outbox.put_nowait({"type": "error", "code": "missing_scope", "message": "Falta el permiso 'chat'."})
                    continue
                if chat_task is not None and not chat_task.done():
                    outbox.put_nowait({"type": "error", "code": "busy", "message": "Ya hay una respuesta en curso."})
                    continue
                chat_task = asyncio.create_task(_ws_chat(frame, outbox))
            elif kind == "cancel":
                await _agent().stop()
            elif kind == "node.register":
                if not info.has_scope("node"):
                    outbox.put_nowait({"type": "error", "code": "missing_scope", "message": "Falta el permiso 'node'."})
                    continue
                await _register_node(info, sid, frame)
                registered_node = True
                outbox.put_nowait({"type": "node.registered", "surfaces": frame.get("surfaces") or []})
            elif kind == "node.result":
                from backend.core.node_manager import get_node_manager

                payload = frame.get("data") if frame.get("ok", True) else {"error": frame.get("error")}
                get_node_manager().resolve_invocation(str(frame.get("request_id") or ""), payload or {})
            elif kind == "node.event":
                logger.info(f"Evento de nodo {info.device_name or info.device_id}: {frame.get('event')}")
            else:
                outbox.put_nowait({"type": "error", "code": "bad_request", "message": f"Tipo desconocido: {kind}"})
    except WebSocketDisconnect:
        pass
    finally:
        client_sinks.unregister(sid)
        writer_task.cancel()
        if registered_node:
            from backend.core.node_manager import get_node_manager

            await get_node_manager().node_disconnected(sid)


async def _ws_chat(frame: dict[str, Any], outbox: asyncio.Queue) -> None:
    request_id = frame.get("id")

    async def forward(item: dict[str, Any]) -> None:
        outbox.put_nowait({**item, "id": request_id})

    try:
        result = await run_chat(str(frame.get("text") or ""), on_event=forward)
        outbox.put_nowait({"type": "done", "id": request_id, **result.to_dict()})
    except ApiError as exc:
        outbox.put_nowait({"type": "error", "id": request_id, "code": exc.code, "message": exc.message})


async def _register_node(info: local_auth.AuthInfo, sid: str, frame: dict[str, Any]) -> None:
    from backend.api.websocket_handler import sio
    from backend.core.node_manager import get_node_manager

    surfaces = [str(s) for s in frame.get("surfaces") or [] if isinstance(s, str)][:50]
    manager = get_node_manager()
    await manager.register_device_node(
        node_id=info.device_id or f"session_{sid[-6:]}",
        name=info.device_name or str(frame.get("device_name") or "Dispositivo"),
        node_type=str(frame.get("platform") or "iot")[:32],
        surfaces=surfaces,
        ws_sid=sid,
        meta=frame.get("meta") if isinstance(frame.get("meta"), dict) else {},
        sio=sio,
    )
