"""
G-Mini Agent — WebSocket handler.
Gestiona la comunicación bidireccional en tiempo real entre Electron y Python.
"""

from __future__ import annotations

import socketio
from loguru import logger

from backend.api.schemas import (
    AgentMessage,
    AgentApprovalEvent,
    AgentStatusEvent,
    AgentStatus,
)

# Crear instancia de Socket.IO (async)
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=["http://127.0.0.1:8765", "http://localhost:8765", "file://"],
    logger=False,
    engineio_logger=False,
    max_http_buffer_size=10_000_000,
)

# Referencia al AgentCore (se inyecta en main.py)
_agent_core = None


def set_agent_core(agent_core) -> None:
    global _agent_core
    _agent_core = agent_core


def _build_gateway_meta(environ: dict | None) -> dict[str, str]:
    payload = environ or {}
    return {
        "user_agent": str(payload.get("HTTP_USER_AGENT") or ""),
        "remote_addr": str(payload.get("REMOTE_ADDR") or ""),
        "origin": str(payload.get("HTTP_ORIGIN") or ""),
    }


# ── Connection Events ────────────────────────────────────────────

@sio.event
async def connect(sid: str, environ: dict) -> None:
    logger.info(f"Cliente conectado: {sid}")
    try:
        from backend.core.gateway_service import get_gateway

        gateway = get_gateway()
        await gateway.register_local_session(
            sid,
            session_key="main",
            display_name="G-Mini App",
            meta=_build_gateway_meta(environ),
        )
    except Exception as exc:
        logger.warning(f"No se pudo registrar la sesion gateway {sid}: {exc}")

    # Auto-cargar la última sesión con mensajes para mantener continuidad
    if _agent_core is not None:
        try:
            sessions = await _agent_core._memory.list_sessions(limit=1)
            if sessions:
                last = sessions[0]
                await _agent_core.load_session(last["session_id"])
                logger.info(f"Sesión anterior restaurada: {last['session_id']} ({last['message_count']} msgs, '{last['title']}')")
                # Notificar al frontend para que renderice el chat restaurado
                # Use all_messages to include display-only activity (system, action, error)
                messages = [
                    {"role": m["role"], "content": m["content"], "timestamp": m.get("timestamp", ""),
                     "message_type": m.get("message_type", "text"), "metadata": m.get("metadata", {})}
                    for m in _agent_core._memory.all_messages
                ]
                await sio.emit(
                    "session:restored",
                    {
                        "session_id": last["session_id"],
                        "title": last.get("title", ""),
                        "messages": messages,
                    },
                    to=sid,
                )
        except Exception as exc:
            logger.warning(f"No se pudo restaurar sesión anterior: {exc}")

    # Enviar estado inicial
    await sio.emit(
        "agent:status",
        AgentStatusEvent(status=AgentStatus.IDLE).model_dump(),
        to=sid,
    )


@sio.event
async def disconnect(sid: str) -> None:
    logger.info(f"Cliente desconectado: {sid}")
    try:
        from backend.core.gateway_service import get_gateway

        await get_gateway().unregister_local_session(sid)
    except Exception as exc:
        logger.warning(f"No se pudo cerrar la sesion gateway {sid}: {exc}")
    try:
        from backend.core.node_manager import get_node_manager
        await get_node_manager().node_disconnected(sid)
    except Exception as exc:
        logger.debug(f"Node disconnect cleanup failed for {sid}: {exc}")


# ── User Events ──────────────────────────────────────────────────

@sio.on("user:message")
async def handle_user_message(sid: str, data: dict) -> None:
    """Recibe un mensaje de texto del usuario."""
    text = data.get("text", "").strip()
    attachments = data.get("attachments", [])

    if not text and not attachments:
        return
    if not text:
        text = "Analiza los adjuntos del usuario y responde en consecuencia."

    logger.info(f"Mensaje del usuario: {text[:80]}...")
    try:
        from backend.core.gateway_service import get_gateway

        await get_gateway().touch_local_session(sid)
    except Exception as exc:
        logger.debug(f"Gateway touch_local_session failed for {sid}: {exc}")

    if _agent_core is None:
        await sio.emit(
            "agent:message",
            AgentMessage(
                text="Error: Agent core no inicializado.",
                type="error",
                done=True,
            ).model_dump(),
            to=sid,
        )
        return

    # Procesar en el agente (streaming)
    # Si el modelo seleccionado es Live API (api_method: 'live'), redirigir via WebSocket Live API.
    # Según docs: https://ai.google.dev/gemini-api/docs/live-guide#sending-text-message
    # La Live API acepta texto como entrada y devuelve audio + transcripción.
    _is_live = False
    try:
        from backend.config import config as _lcfg
        _current_provider = _lcfg.get("model_router", "default_provider", default="") or ""
        _current_model = _lcfg.get("model_router", "default_model", default="") or ""
        if _current_provider == "google" and _current_model:
            from pathlib import Path as _LPath
            import yaml as _lyaml
            _my = _LPath(__file__).resolve().parent.parent.parent / "data" / "models.yaml"
            with open(_my, "r", encoding="utf-8") as _lf:
                _lcat = _lyaml.safe_load(_lf) or {}
            _lgm = _lcat.get("llm", {}).get("google", {})
            _lmd = _lgm.get(_current_model, {}) if isinstance(_lgm, dict) else {}
            _is_live = _lmd.get("api_method", "") == "live"
    except Exception:
        pass
    if _is_live:
        await _agent_core.process_message_live(sid, text)
        return
    await _agent_core.process_message(sid, text, attachments)


@sio.on("user:command")
async def handle_user_command(sid: str, data: dict) -> None:
    """Recibe un comando de control (start, stop, pause, voice_start, etc.)."""
    action = data.get("action", "")
    logger.info(f"Comando del usuario: {action}")
    try:
        from backend.core.gateway_service import get_gateway

        await get_gateway().touch_local_session(sid)
    except Exception as exc:
        logger.debug(f"Gateway touch_local_session failed for {sid}: {exc}")

    if _agent_core is None:
        return

    match action:
        case "stop":
            await _agent_core.stop()
        case "pause":
            await _agent_core.pause()
        case "start":
            await _agent_core.resume()
        case "approve_pending":
            await _agent_core.approve_pending(sid)
        case "cancel_pending":
            await _agent_core.reject_pending(sid)
        case "voice_start":
            provider = data.get("provider", "openai")
            logger.info(f"Voice start via command: provider={provider}")
            try:
                success = await _agent_core.start_realtime_voice(sid, provider)
                if success:
                    await sio.emit("agent:status", {"status": "realtime_active", "provider": provider}, to=sid)
                else:
                    await sio.emit("agent:message", AgentMessage(text="No se pudo iniciar voz RT.", type="error", done=True).model_dump(), to=sid)
            except Exception as exc:
                logger.error(f"Error en voice_start command: {exc}")
        case "voice_stop":
            logger.info("Voice stop via command")
            try:
                await _agent_core.stop_realtime_voice()
                await sio.emit("agent:status", {"status": "realtime_stopped"}, to=sid)
            except Exception as exc:
                logger.error(f"Error en voice_stop command: {exc}")
        case _:
            logger.warning(f"Comando no reconocido: {action}")


@sio.on("user:config")
async def handle_user_config(sid: str, data: dict) -> None:
    """Recibe un cambio de configuración desde el frontend."""
    from backend.config import config

    _EDITABLE_SECTIONS = {
        "model_router", "agent", "character", "app", "tts", "voice",
        "scheduler", "gateway", "vision", "canvas",
    }

    section = data.get("section", "")
    key = data.get("key", "")
    value = data.get("value")

    if section and key:
        if section not in _EDITABLE_SECTIONS:
            logger.warning(f"Config: sección '{section}' no permitida desde frontend (sid={sid})")
            await sio.emit("config:error", {"error": f"Sección '{section}' no es editable"}, to=sid)
            return
        config.set(section, key, value=value)
        logger.info(f"Config actualizada: {section}.{key} = {value}")
        try:
            from backend.core.gateway_service import get_gateway

            await get_gateway().touch_local_session(sid)
        except Exception as exc:
            logger.debug(f"Gateway touch_local_session failed for {sid}: {exc}")
        await sio.emit("config:updated", {"section": section, "key": key, "value": value}, to=sid)


@sio.on("user:stt_audio")
async def handle_stt_audio(sid: str, data: dict) -> None:
    """Recibe audio del micrófono para STT (Fase 3)."""
    import base64

    if not _agent_core or not _agent_core.voice:
        await sio.emit("agent:stt_result", {"text": "", "error": "Voice engine no disponible"}, to=sid)
        return

    audio_b64 = data.get("audio")
    if not audio_b64:
        await sio.emit("agent:stt_result", {"text": "", "error": "No audio recibido"}, to=sid)
        return

    try:
        audio_bytes = base64.b64decode(audio_b64)
        text = await _agent_core.voice.transcribe(audio_bytes)
        await sio.emit("agent:stt_result", {"text": text, "error": None}, to=sid)
    except Exception as e:
        logger.error(f"STT error: {e}")
        await sio.emit("agent:stt_result", {"text": "", "error": str(e)}, to=sid)


@sio.on("user:realtime_audio")
async def handle_realtime_audio(sid: str, data: dict) -> None:
    """Recibe audio chunk PCM16 (base64) para voz real-time."""
    if _agent_core is None:
        return

    audio_b64 = data.get("audio")
    if not audio_b64:
        return

    try:
        import base64

        audio_bytes = base64.b64decode(audio_b64)
        await _agent_core.send_realtime_audio(audio_bytes)
    except Exception as exc:
        logger.error(f"Error procesando audio real-time: {exc}")


@sio.on("user:realtime_start")
async def handle_realtime_start(sid: str, data: dict) -> None:
    """Inicia una sesión de voz en tiempo real con un proveedor RT o simulado."""
    if _agent_core is None:
        await sio.emit(
            "agent:status",
            AgentStatusEvent(status=AgentStatus.IDLE).model_dump(),
            to=sid,
        )
        return

    provider = str(data.get("provider", "")).strip().lower()
    requested_mode = str(data.get("mode", "")).strip().lower()

    # Determinar modo: nativo o simulado
    mode = "simulated"  # default a simulado
    rt_provider = ""

    if requested_mode == "native" or not requested_mode:
        from backend.voice.realtime import RealTimeVoice
        from backend.config import config as app_config

        current_model = app_config.get("model_router", "default_model", default="") or ""
        google_backend = app_config.get("providers", "google", "backend", default="ai_studio")

        def _model_is_rt(prov: str) -> bool:
            """Verifica si el modelo actual es un modelo RT nativo del provider."""
            if prov == "google" and google_backend == "vertex_ai":
                try:
                    from pathlib import Path as _Prt
                    import yaml as _yrt
                    _mf = _Prt(__file__).resolve().parent.parent.parent / "data" / "models.yaml"
                    with open(_mf, "r", encoding="utf-8") as _fh:
                        _cat = _yrt.safe_load(_fh) or {}
                    _gm = _cat.get("llm", {}).get("google", {})
                    _md = _gm.get(current_model, {}) if isinstance(_gm, dict) else {}
                    has_live = bool(_md.get("features", {}).get("live_api", False))
                    return has_live and bool(app_config.get("providers", "google", "project_id", default=""))
                except Exception:
                    return False
            rt_info = RealTimeVoice.get_realtime_providers().get(prov, {})
            return current_model in rt_info.get("models", [])

        # Intentar resolver provider RT nativo
        if provider in ("openai", "google", "xai"):
            resolved = RealTimeVoice.resolve_rt_provider(provider)
            if resolved and _model_is_rt(resolved):
                rt_provider = resolved
                mode = "native"
        else:
            current_provider = app_config.get("model_router", "default_provider", default="") or ""
            resolved = RealTimeVoice.resolve_rt_provider(current_provider)
            if resolved and _model_is_rt(resolved):
                rt_provider = resolved
                mode = "native"

    if requested_mode == "simulated":
        mode = "simulated"

    # Si no hay RT nativo y no se puede simular, error
    if mode == "native" and not rt_provider:
        mode = "simulated"

    if mode == "simulated":
        # Verificar que tengamos STT para modo simulado
        has_stt = bool(_agent_core.voice and _agent_core.voice.stt_available)
        if not has_stt:
            await sio.emit(
                "agent:message",
                AgentMessage(
                    text="No hay motor de reconocimiento de voz (STT) disponible. "
                         "Instala faster-whisper o configura una API key de un proveedor RT (OpenAI, Google, xAI).",
                    type="error",
                    done=True,
                ).model_dump(),
                to=sid,
            )
            return

    effective_provider = rt_provider if mode == "native" else provider
    logger.info(f"Realtime voice start: mode={mode}, provider={effective_provider}, sid={sid}")

    try:
        voice = str(data.get("voice", "")).strip()
        # Notificar al frontend que estamos conectando (para mostrar indicador de carga)
        await sio.emit(
            "agent:status",
            {"status": "realtime_connecting", "provider": effective_provider, "mode": mode},
            to=sid,
        )
        success = await _agent_core.start_realtime_voice(
            sid, effective_provider, voice=voice, mode=mode,
        )
        if success:
            await sio.emit(
                "agent:status",
                {"status": "realtime_active", "provider": effective_provider, "mode": mode},
                to=sid,
            )
        else:
            await sio.emit(
                "agent:message",
                AgentMessage(
                    text="No se pudo iniciar la sesión de voz. "
                         + ("Verifica que el proveedor RT esté configurado." if mode == "native"
                            else "Verifica que el motor STT y TTS estén disponibles."),
                    type="error",
                    done=True,
                ).model_dump(),
                to=sid,
            )
    except Exception as exc:
        logger.error(f"Error iniciando realtime voice ({mode}): {exc}")
        await sio.emit(
            "agent:message",
            AgentMessage(text=f"Error al iniciar la sesión de voz: {exc}", type="error", done=True).model_dump(),
            to=sid,
        )


@sio.on("user:realtime_stop")
async def handle_realtime_stop(sid: str, data: dict) -> None:
    """Detiene la sesión de voz en tiempo real."""
    if _agent_core is None:
        return

    logger.info(f"Realtime voice stop: sid={sid}")
    try:
        await _agent_core.stop_realtime_voice()
        await sio.emit(
            "agent:status",
            {"status": "realtime_stopped"},
            to=sid,
        )
    except Exception as exc:
        logger.error(f"Error deteniendo realtime voice: {exc}")


@sio.on("user:screen_stream_toggle")
async def handle_screen_stream_toggle(sid: str, data: dict) -> None:
    """Activa o desactiva el streaming de pantalla hacia la sesión Live API."""
    if _agent_core is None:
        return

    enable = bool(data.get("enable", True))
    try:
        if enable:
            success = await _agent_core.start_screen_stream()
            await sio.emit(
                "agent:screen_stream_status",
                {"active": success},
                to=sid,
            )
            if not success:
                await sio.emit(
                    "agent:message",
                    AgentMessage(
                        text="No se pudo iniciar el streaming de pantalla. "
                             "Solo disponible con Google Live API y sesión RT activa.",
                        type="error",
                        done=True,
                    ).model_dump(),
                    to=sid,
                )
        else:
            await _agent_core.stop_screen_stream()
            await sio.emit(
                "agent:screen_stream_status",
                {"active": False},
                to=sid,
            )
    except Exception as exc:
        logger.error(f"Error toggling screen stream: {exc}")
        await sio.emit(
            "agent:screen_stream_status",
            {"active": False},
            to=sid,
        )


@sio.on("user:check_realtime")
async def handle_check_realtime(sid: str, data: dict) -> None:
    """
    Comprueba si el modelo actual soporta voz en tiempo real.

    Lógica:
    - Si el modelo tiene live_api=true en models.yaml (Google) O está en realtime_models.yaml
      (OpenAI, xAI) → mode='native' (botón 📞)
    - Si no, pero hay STT disponible → mode='simulated' STT→LLM→TTS (botón 🎙️)
    - Si nada está disponible → available=False (botón oculto)
    """
    from pathlib import Path as _Path
    import yaml as _yaml
    from backend.voice.realtime import RealTimeVoice
    from backend.config import config as app_config

    payload = data or {}
    current_provider = str(payload.get("provider", "")).strip().lower()
    if not current_provider:
        current_provider = app_config.get("model_router", "default_provider", default="") or ""

    # Preferir modelo enviado por el cliente (UI); fallback a config guardada
    current_model = str(payload.get("model", "")).strip()
    if not current_model:
        current_model = app_config.get("model_router", "default_model", default="") or ""

    # ── 1. Determinar si el modelo tiene Live API nativa ─────────────
    model_is_native = False
    _model_data = {}

    # Para Google: verificar si el modelo seleccionado tiene live_api en models.yaml
    if current_provider == "google":
        google_backend = app_config.get("providers", "google", "backend", default="ai_studio")

        # Consultar live_api del modelo en models.yaml (aplica a AI Studio y Vertex AI)
        try:
            _models_yaml = _Path(__file__).resolve().parent.parent.parent / "data" / "models.yaml"
            with open(_models_yaml, "r", encoding="utf-8") as _f:
                _catalog = _yaml.safe_load(_f) or {}
            _google_models = _catalog.get("llm", {}).get("google", {})
            _model_data = _google_models.get(current_model, {}) if isinstance(_google_models, dict) else {}
            model_is_native = bool(_model_data.get("features", {}).get("live_api", False))
        except Exception as _exc:
            logger.warning(f"No se pudo leer models.yaml para check_realtime: {_exc}")
            rt_google = RealTimeVoice.get_realtime_providers().get("google", {})
            model_is_native = current_model in rt_google.get("models", [])

        # Vertex AI requiere además project_id configurado
        if google_backend == "vertex_ai" and model_is_native:
            project_id = app_config.get("providers", "google", "project_id", default="")
            model_is_native = bool(project_id)
    else:
        # Para OpenAI, xAI y otros: usar realtime_models.yaml
        if current_provider in RealTimeVoice.get_realtime_providers():
            api_key_map = {"openai": "openai_api", "xai": "xai_api"}
            key_name = api_key_map.get(current_provider)
            has_key = bool(key_name and app_config.get_api_key(key_name))
            if has_key:
                rt_info = RealTimeVoice.get_realtime_providers().get(current_provider, {})
                model_is_native = current_model in rt_info.get("models", [])

    logger.debug(
        f"check_realtime: provider={current_provider}, model={current_model}, "
        f"native={model_is_native}"
    )

    if model_is_native:
        # Modo nativo: el modelo seleccionado tiene Live API real
        voices = RealTimeVoice.GOOGLE_VOICES if current_provider == "google" else []
        # Leer features del modelo para el frontend (video streaming, grounding search)
        _grounding = False
        if current_provider == "google":
            try:
                _grounding = bool(_model_data.get("features", {}).get("grounding_search", False))
            except Exception:
                pass
        await sio.emit(
            "agent:realtime_available",
            {
                "available": True,
                "mode": "native",
                "provider": current_provider,
                "voices": voices,
                "supports_video": True,  # Live API siempre soporta video input
                "supports_grounding_search": _grounding,
            },
            to=sid,
        )
    else:
        # Modo simulado: STT → modelo de texto → TTS
        # Disponible para TODOS los modelos/proveedores siempre que haya STT
        has_stt = bool(_agent_core and _agent_core.voice and _agent_core.voice.stt_available)
        has_tts = bool(_agent_core and _agent_core.voice and _agent_core.voice.tts_available)

        if has_stt:
            await sio.emit(
                "agent:realtime_available",
                {
                    "available": True,
                    "mode": "simulated",
                    "provider": current_provider,
                    "voices": [],
                    "tts_available": has_tts,
                },
                to=sid,
            )
        else:
            await sio.emit(
                "agent:realtime_available",
                {
                    "available": False,
                    "mode": "none",
                    "provider": "",
                    "voices": [],
                },
                to=sid,
            )


# ── Helper functions para emitir eventos ─────────────────────────

async def emit_message(sid: str, text: str, msg_type: str = "text", done: bool = False) -> None:
    """Emite un mensaje del agente al frontend."""
    try:
        from backend.core.gateway_service import get_gateway

        if await get_gateway().forward_agent_message(sid, text, msg_type=msg_type, done=done):
            return
    except Exception as exc:
        logger.warning(f"No se pudo redirigir mensaje al gateway remoto: {exc}")
    await sio.emit(
        "agent:message",
        AgentMessage(text=text, type=msg_type, done=done).model_dump(),
        to=sid,
    )


async def emit_status(sid: str, status: AgentStatus) -> None:
    """Emite un cambio de estado del agente."""
    try:
        from backend.core.gateway_service import get_gateway

        status_value = status.value if hasattr(status, "value") else status
        if await get_gateway().forward_agent_status(sid, status_value):
            return
    except Exception as exc:
        logger.warning(f"No se pudo redirigir estado al gateway remoto: {exc}")
    await sio.emit(
        "agent:status",
        AgentStatusEvent(status=status).model_dump(),
        to=sid,
    )


async def emit_message_chunk(sid: str, text: str) -> None:
    """Emite un chunk de streaming del agente."""
    try:
        from backend.core.gateway_service import get_gateway

        if await get_gateway().forward_agent_message(sid, text, msg_type="text", done=False):
            return
    except Exception as exc:
        logger.warning(f"No se pudo redirigir chunk al gateway remoto: {exc}")
    await sio.emit(
        "agent:message",
        AgentMessage(text=text, type="text", done=False).model_dump(),
        to=sid,
    )


async def emit_message_done(sid: str) -> None:
    """Señala fin del streaming."""
    try:
        from backend.core.gateway_service import get_gateway

        if await get_gateway().forward_agent_message(sid, "", msg_type="text", done=True):
            return
    except Exception as exc:
        logger.warning(f"No se pudo cerrar stream remoto en gateway: {exc}")
    await sio.emit(
        "agent:message",
        AgentMessage(text="", type="text", done=True).model_dump(),
        to=sid,
    )


async def emit_emotion(sid: str, emotion: str) -> None:
    """Emite un cambio de emocion para animar la skin VRM."""
    await sio.emit("agent:emotion", {"emotion": emotion}, to=sid)


async def emit_screenshot(sid: str, image_b64: str, *, caption: str = "") -> None:
    """Emite una captura del agente al frontend o al gateway remoto.

    Comprime la imagen a JPEG para que el frontend pueda renderizarla
    de forma fiable sin exceder los límites de data-URI de Chromium.
    """
    if not image_b64 or not isinstance(image_b64, str) or len(image_b64) < 100:
        logger.warning("emit_screenshot: imagen base64 vacía o inválida, omitiendo emisión")
        return

    # ── Comprimir a JPEG para el frontend ────────────────────────
    frontend_b64 = image_b64
    original_size_kb = len(image_b64) * 3 // 4 // 1024  # approx decoded size
    try:
        import base64 as _b64
        from io import BytesIO as _BytesIO
        from PIL import Image as _PILImage

        # Decodificar la imagen original
        raw_bytes = _b64.b64decode(image_b64)
        img = _PILImage.open(_BytesIO(raw_bytes))

        # Convertir a RGB si tiene canal alfa (PNG con transparencia)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")

        # Redimensionar si es muy grande (max 1600px de ancho para el frontend)
        max_w = 1600
        if img.width > max_w:
            ratio = max_w / img.width
            new_h = int(img.height * ratio)
            img = img.resize((max_w, new_h), _PILImage.LANCZOS)

        # Comprimir a JPEG
        buf = _BytesIO()
        img.save(buf, format="JPEG", quality=72, optimize=True)
        frontend_b64 = _b64.b64encode(buf.getvalue()).decode("ascii")
        compressed_size_kb = len(buf.getvalue()) // 1024

        logger.debug(
            f"emit_screenshot: comprimido {original_size_kb}KB → {compressed_size_kb}KB "
            f"(JPEG q72, {img.width}x{img.height})"
        )
    except Exception as comp_exc:
        logger.warning(f"emit_screenshot: no se pudo comprimir, enviando original: {comp_exc}")
        frontend_b64 = image_b64

    try:
        from backend.core.gateway_service import get_gateway

        if await get_gateway().forward_agent_screenshot(sid, image_b64, caption=caption):
            return
    except Exception as exc:
        logger.warning(f"No se pudo redirigir screenshot al gateway remoto: {exc}")
    await sio.emit(
        "agent:screenshot",
        {"image": frontend_b64, "caption": caption},
        to=sid,
    )


async def emit_media(sid: str, media_type: str, filename: str, url: str, **extra) -> None:
    """Emite un archivo multimedia generado al frontend para reproducción inline."""
    await sio.emit(
        "agent:media",
        {"type": media_type, "filename": filename, "url": url, **extra},
        to=sid,
    )


async def emit_approval_state(
    sid: str,
    pending: bool,
    summary: str = "",
    findings: list[dict] | None = None,
    mode: str | None = None,
    mode_name: str | None = None,
    kind: str = "approval",
    decision: str | None = None,
) -> None:
    """Emite el estado de aprobacion pendiente al frontend."""
    try:
        from backend.core.gateway_service import get_gateway

        if await get_gateway().forward_agent_approval(
            sid,
            pending=pending,
            summary=summary,
            findings=findings or [],
            kind=kind,
            decision=decision,
            mode_name=mode_name,
        ):
            return
    except Exception as exc:
        logger.warning(f"No se pudo redirigir aprobacion al gateway remoto: {exc}")
    await sio.emit(
        "agent:approval",
        AgentApprovalEvent(
            pending=pending,
            summary=summary,
            findings=findings or [],
            mode=mode,
            mode_name=mode_name,
            kind=kind,
            decision=decision,
        ).model_dump(),
        to=sid,
    )


async def emit_subagents_state(
    sid: str,
    items: list[dict],
    last_event: dict | None = None,
) -> None:
    """Emite el estado actual de los sub-agentes al frontend."""
    await sio.emit(
        "agent:subagents",
        {
            "items": items,
            "active_count": sum(1 for item in items if item.get("status") in {"queued", "running"}),
            "last_event": last_event,
        },
        to=sid,
    )


# ── Node Events (Phase 7) ────────────────────────────────────────

@sio.event
async def node_pair(sid: str, data: dict) -> None:
    """Un nodo remoto completa el emparejamiento enviando su token."""
    try:
        from backend.core.node_manager import get_node_manager
        mgr = get_node_manager()
        token = data.get("pairing_token", "")
        surfaces = data.get("surfaces", [])
        meta = data.get("meta", {})
        node = await mgr.complete_pairing(token, sid, surfaces=surfaces, meta=meta)
        if node:
            await sio.emit("node:paired", node.to_dict(), to=sid)
            logger.info(f"Nodo emparejado via WS: {node.name} (sid={sid})")
        else:
            await sio.emit("node:pair_error", {"error": "Token inválido o expirado"}, to=sid)
    except Exception as exc:
        logger.warning(f"Error en node_pair: {exc}")
        await sio.emit("node:pair_error", {"error": str(exc)}, to=sid)


@sio.event
async def node_reconnect(sid: str, data: dict) -> None:
    """Un nodo previamente emparejado se reconecta con su node_id."""
    try:
        from backend.core.node_manager import get_node_manager
        mgr = get_node_manager()
        node_id = data.get("node_id", "")
        node = await mgr.node_connected(node_id, sid)
        if node:
            await sio.emit("node:reconnected", node.to_dict(), to=sid)
        else:
            await sio.emit("node:pair_error", {"error": "Nodo no registrado o baneado"}, to=sid)
    except Exception as exc:
        logger.warning(f"Error en node_reconnect: {exc}")


@sio.event
async def node_heartbeat(sid: str, data: dict) -> None:
    """Heartbeat periódico del nodo."""
    try:
        from backend.core.node_manager import get_node_manager
        mgr = get_node_manager()
        await mgr.heartbeat(sid)
    except Exception as exc:
        logger.warning(f"Heartbeat failed for node {sid}: {exc}")


@sio.event
async def node_invoke_result(sid: str, data: dict) -> None:
    """Resultado de una invocación de superficie en un nodo remoto."""
    try:
        from backend.core.node_manager import get_node_manager
        mgr = get_node_manager()
        request_id = data.get("request_id", "")
        result = data.get("result", {})
        mgr.resolve_invocation(request_id, result)
    except Exception as exc:
        logger.warning(f"Error en node_invoke_result: {exc}")


# ── Canvas Events (Phase 8) ──────────────────────────────────────

@sio.event
async def canvas_subscribe(sid: str, data: dict) -> None:
    """Suscribe un cliente a actualizaciones en vivo de un canvas."""
    try:
        from backend.core.canvas import get_canvas_service
        svc = get_canvas_service()
        canvas_id = data.get("canvas_id", "")
        svc.subscribe(canvas_id, sid)
        canvas = await svc.get_canvas(canvas_id)
        if canvas:
            await sio.emit("canvas:snapshot", canvas.to_dict(), to=sid)
    except Exception as exc:
        logger.warning(f"Error en canvas_subscribe: {exc}")


@sio.event
async def canvas_unsubscribe(sid: str, data: dict) -> None:
    """Desuscribe un cliente de un canvas."""
    try:
        from backend.core.canvas import get_canvas_service
        svc = get_canvas_service()
        canvas_id = data.get("canvas_id", "")
        svc.unsubscribe(canvas_id, sid)
    except Exception as exc:
        logger.debug(f"Error en canvas_unsubscribe (ignorado): {exc}")
