"""
G-Mini Agent - Voice Engine.
TTS (Text-to-Speech), STT (Speech-to-Text) y Real-Time Voice.
"""

from __future__ import annotations

import asyncio
import base64
import io
import re
import wave
from copy import deepcopy
from typing import Any, AsyncGenerator

from loguru import logger

from backend.config import config

# -- TTS Engines --------------------------------------------------------------
# Los SDK pesados se importan recién al elegir ese motor: importar MeloTTS
# carga torch y costaba 10-50 s de arranque aunque el usuario no lo usara.

import importlib.util

from backend.voice import tts_engines

HAS_MELOTTS = importlib.util.find_spec("melo") is not None
HAS_ELEVENLABS = importlib.util.find_spec("elevenlabs") is not None

DEFAULT_TTS_ENGINE = "edge" if tts_engines.HAS_EDGE_TTS else "webspeech"
DEFAULT_GOOGLE_TTS_ENGINE = "gemini-3.8-flash-tts"
DEFAULT_ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
DEFAULT_GOOGLE_VOICE = "Kore"
DEFAULT_EDGE_VOICE = "es-PE-CamilaNeural"
HEAVY_TTS_ENGINES = {"melotts"}

# Voces de Gemini TTS (30, guía oficial de estilos).
GOOGLE_VOICE_CATALOG: list[dict] = tts_engines.GEMINI_VOICES

TTS_ENGINE_CATALOG: dict[str, dict[str, Any]] = {
    "edge": {
        "label": "Voces neuronales de Microsoft Edge (gratis)",
        "provider": "edge",
        "online": True,
        "supports_numeric_speed": True,
    },
    "openai-tts": {
        "label": "OpenAI gpt-4o-mini-tts (online)",
        "provider": "openai",
        "online": True,
        "supports_numeric_speed": True,
    },
    "gemini-3.8-flash-tts": {
        "label": "Gemini 3.8 Flash TTS (online)",
        "provider": "google",
        "online": True,
        "supports_numeric_speed": False,
    },
    "gemini-3.8-flash-lite-tts": {
        "label": "Gemini 3.8 Flash Lite TTS (online, económico)",
        "provider": "google",
        "online": True,
        "supports_numeric_speed": False,
    },
    "melotts": {
        "label": "MeloTTS (offline)",
        "provider": "local",
        "online": False,
        "supports_numeric_speed": True,
    },
    "elevenlabs": {
        "label": "ElevenLabs (online)",
        "provider": "elevenlabs",
        "online": True,
        "supports_numeric_speed": True,
    },
    "webspeech": {
        "label": "Navegador (Web Speech)",
        "provider": "browser",
        "online": False,   # usa voces del SO/navegador, sin red
        "supports_numeric_speed": True,
    },
    "none": {
        "label": "Desactivado",
        "provider": "none",
        "online": False,
        "supports_numeric_speed": False,
    },
}

TTS_ENGINE_LEGACY_MAP = {
    # Retirados por Google el 2026-11-17: se migran solos al modelo vigente.
    "gemini-2.5-pro-tts": DEFAULT_GOOGLE_TTS_ENGINE,
    "gemini-2.5-pro-preview-tts": DEFAULT_GOOGLE_TTS_ENGINE,
    "gemini-2.5-flash-tts": DEFAULT_GOOGLE_TTS_ENGINE,
    "gemini-2.5-flash-preview-tts": DEFAULT_GOOGLE_TTS_ENGINE,
    "gemini-3.1-flash-tts-preview": DEFAULT_GOOGLE_TTS_ENGINE,
    "gemini-2.5-flash-lite-preview-tts": "gemini-3.8-flash-lite-tts",
    "chirp_3": DEFAULT_GOOGLE_TTS_ENGINE,
    "chirp_2": DEFAULT_GOOGLE_TTS_ENGINE,
    "openai": "openai-tts",
    "edge-tts": "edge",
}

GOOGLE_TTS_ENGINES = {
    engine_id
    for engine_id, meta in TTS_ENGINE_CATALOG.items()
    if meta.get("provider") == "google"
}


def list_tts_engines() -> list[dict[str, Any]]:
    return [
        {
            "id": engine_id,
            **deepcopy(meta),
        }
        for engine_id, meta in TTS_ENGINE_CATALOG.items()
    ]


def get_tts_engine_descriptor(engine_id: str) -> dict[str, Any]:
    if engine_id in TTS_ENGINE_CATALOG:
        return {"id": engine_id, **deepcopy(TTS_ENGINE_CATALOG[engine_id])}
    return {
        "id": engine_id,
        "label": engine_id or "Desconocido",
        "provider": "unknown",
        "online": False,
        "supports_numeric_speed": False,
    }


def is_google_tts_engine(engine_id: str | None) -> bool:
    return str(engine_id or "").strip() in GOOGLE_TTS_ENGINES


def normalize_tts_engine(engine_value: Any) -> tuple[str, str | None]:
    raw = str(engine_value or "").strip()
    if not raw:
        return DEFAULT_TTS_ENGINE, None
    if raw in TTS_ENGINE_CATALOG:
        return raw, None
    if raw in TTS_ENGINE_LEGACY_MAP:
        mapped = TTS_ENGINE_LEGACY_MAP[raw]
        return mapped, f"Motor TTS legado '{raw}' migrado a '{mapped}'."
    return raw, None


def migrate_voice_config() -> list[str]:
    warnings: list[str] = []

    raw_tts_engine = config.get("voice", "tts_primary", default=DEFAULT_TTS_ENGINE)
    normalized_engine, tts_warning = normalize_tts_engine(raw_tts_engine)
    if tts_warning and normalized_engine in TTS_ENGINE_CATALOG:
        config.set("voice", "tts_primary", value=normalized_engine)
        warnings.append(tts_warning)

    legacy_voice_id = str(
        config.get("voice", "elevenlabs_default_voice", default="") or ""
    ).strip()
    canonical_voice_id = str(
        config.get("voice", "elevenlabs_voice_id", default="") or ""
    ).strip()
    if legacy_voice_id and legacy_voice_id != canonical_voice_id:
        config.set("voice", "elevenlabs_voice_id", value=legacy_voice_id)
        warnings.append(
            "Configuracion legacy de ElevenLabs migrada a 'voice.elevenlabs_voice_id'."
        )
    if config.get("voice", "elevenlabs_default_voice", default=None) is not None:
        config.unset("voice", "elevenlabs_default_voice")

    return warnings


# -- STT Engine ---------------------------------------------------------------

HAS_WHISPER = False
_WhisperModel = None


def _lazy_load_whisper() -> None:
    """Importa faster_whisper de forma lazy para evitar bloqueo al inicio."""
    global HAS_WHISPER, _WhisperModel
    if _WhisperModel is not None:
        return
    try:
        from faster_whisper import WhisperModel

        _WhisperModel = WhisperModel
        HAS_WHISPER = True
    except ImportError:
        HAS_WHISPER = False


def _extract_sample_rate(mime_type: str, default: int = 24000) -> int:
    match = re.search(r"rate=(\d+)", mime_type or "", flags=re.IGNORECASE)
    if match:
        try:
            return max(1, int(match.group(1)))
        except ValueError:
            return default
    return default


def _wrap_pcm16_as_wav(audio_bytes: bytes, sample_rate: int = 24000) -> bytes:
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_bytes)
        return buffer.getvalue()


def _audio_for_whisper(audio_bytes: bytes):
    """WAV/FLAC/OGG/MP3 -> float32 mono a 16 kHz con soundfile.

    faster-whisper decodifica con PyAV y sus versiones nuevas rompieron la
    llamada (`metadata_errors`); con el arreglo ya decodificado no depende de
    eso. Si soundfile no reconoce el formato, se pasa el original.
    """
    try:
        import numpy as np
        import soundfile as sf

        data, rate = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
        if rate != 16000 and len(mono):
            count = int(len(mono) * 16000 / rate)
            mono = np.interp(np.linspace(0, len(mono) - 1, count), np.arange(len(mono)), mono)
        return np.ascontiguousarray(mono, dtype=np.float32)
    except Exception:
        return io.BytesIO(audio_bytes)


class VoiceEngine:
    """
    Motor de voz del agente.
    - TTS: MeloTTS (offline), ElevenLabs (online), Gemini TTS (online)
    - STT: Faster-Whisper (offline)
    """

    _TTS_CACHE_MAX = 128

    def __init__(self):
        self._tts_engine: str = "none"
        self._requested_tts_engine: str = "none"
        self._tts_runtime_status: dict[str, Any] = {}
        self._stt_model: Any = None
        self._melo_model: Any = None
        self._melo_speaker_ids: dict[str, Any] = {}
        self._melo_language: str = "ES"
        self._eleven_client: Any = None
        self._google_client: Any = None
        self._initialized = False
        self._tts_cache: dict[str, bytes] = {}
        self._tts_loading: asyncio.Task | None = None
        self._stt_loading: asyncio.Task | None = None
        self._set_tts_status(
            requested_engine="none",
            active_engine="none",
            available=False,
            reason="not_initialized",
            message="VoiceEngine no inicializado.",
        )

    async def initialize(self) -> None:
        """Inicializa los motores de voz. Los modelos locales (MeloTTS, Whisper)
        cargan en segundo plano para no retrasar el arranque del backend."""
        stt_enabled = bool(config.get("voice", "stt_enabled", default=True))
        await self.reload(reload_stt=stt_enabled, background=True)
        logger.info(f"VoiceEngine inicializado (TTS: {self._requested_tts_engine})")

    async def reload(self, *, reload_stt: bool = False, background: bool = False) -> None:
        """Recarga la configuracion de voz sin recrear AgentCore."""
        raw_requested_engine = config.get("voice", "tts_primary", default=DEFAULT_TTS_ENGINE)
        migration_warnings = migrate_voice_config()
        requested_engine, normalization_warning = normalize_tts_engine(
            raw_requested_engine
        )
        warnings = list(migration_warnings)
        if normalization_warning and normalization_warning not in warnings:
            warnings.append(normalization_warning)

        logger.info(
            "VoiceEngine.reload start: "
            f"raw_tts_primary={raw_requested_engine!r}, "
            f"normalized_tts_primary={requested_engine}, "
            f"reload_stt={reload_stt}, "
            f"migration_warnings={migration_warnings}, "
            f"normalization_warning={normalization_warning}"
        )

        self._reset_tts_runtime()
        if background and requested_engine in HEAVY_TTS_ENGINES:
            self._requested_tts_engine = requested_engine
            self._set_tts_status(
                requested_engine=requested_engine, active_engine="none", available=False,
                reason="loading", message="Cargando el modelo de voz local...", warnings=warnings,
            )
            self._tts_loading = asyncio.create_task(self._init_tts(requested_engine, warnings=warnings))
        else:
            await self._init_tts(requested_engine, warnings=warnings)

        if reload_stt:
            self._stt_model = None
            if bool(config.get("voice", "stt_enabled", default=True)):
                if background:
                    self._stt_loading = asyncio.create_task(self._init_stt())
                else:
                    await self._init_stt()

        self._initialized = True
        logger.info(
            "VoiceEngine.reload done: "
            f"requested_engine={self._tts_runtime_status.get('requested_engine')}, "
            f"active_engine={self._tts_runtime_status.get('active_engine')}, "
            f"available={self._tts_runtime_status.get('available')}, "
            f"reason={self._tts_runtime_status.get('reason')}, "
            f"message={self._tts_runtime_status.get('message')}, "
            f"warnings={self._tts_runtime_status.get('warnings')}"
        )

    def _reset_tts_runtime(self) -> None:
        if self._tts_loading is not None and not self._tts_loading.done():
            self._tts_loading.cancel()
        self._tts_loading = None
        self._tts_engine = "none"
        self._requested_tts_engine = "none"
        self._melo_model = None
        self._melo_speaker_ids = {}
        self._eleven_client = None
        self._google_client = None
        self._tts_cache.clear()

    def _set_tts_status(
        self,
        *,
        requested_engine: str,
        active_engine: str,
        available: bool,
        reason: str,
        message: str,
        warnings: list[str] | None = None,
    ) -> None:
        requested_meta = get_tts_engine_descriptor(requested_engine)
        active_meta = get_tts_engine_descriptor(active_engine)
        self._tts_runtime_status = {
            "requested_engine": requested_engine,
            "requested_label": requested_meta.get("label", requested_engine),
            "active_engine": active_engine,
            "active_label": active_meta.get("label", active_engine),
            "available": available,
            "reason": reason,
            "message": message,
            "warnings": list(warnings or []),
            "supports_numeric_speed": bool(
                requested_meta.get("supports_numeric_speed", False)
            ),
            "provider": requested_meta.get("provider", "unknown"),
        }

    async def _init_tts(self, preference: str, *, warnings: list[str] | None = None) -> None:
        """Inicializa el motor TTS solicitado sin aplicar fallback silencioso."""
        warnings = list(warnings or [])
        self._requested_tts_engine = preference
        descriptor = get_tts_engine_descriptor(preference)
        provider = descriptor.get("provider", "unknown")
        logger.info(
            "VoiceEngine._init_tts start: "
            f"preference={preference}, "
            f"provider={provider}, "
            f"warnings={warnings}"
        )

        if preference == "none":
            logger.info("TTS: Desactivado explicitamente")
            self._set_tts_status(
                requested_engine=preference,
                active_engine="none",
                available=False,
                reason="disabled",
                message="El motor TTS esta desactivado.",
                warnings=warnings,
            )
            logger.info("VoiceEngine._init_tts resolved disabled preference.")
            return

        if preference not in TTS_ENGINE_CATALOG:
            logger.warning(f"TTS: Motor no soportado '{preference}'")
            self._set_tts_status(
                requested_engine=preference,
                active_engine="none",
                available=False,
                reason="unsupported_model",
                message=f"El motor TTS '{preference}' no esta soportado.",
                warnings=warnings,
            )
            logger.warning(
                "VoiceEngine._init_tts rejected unsupported engine: "
                f"preference={preference}, provider={provider}"
            )
            return

        if preference == "edge":
            ok, reason, message = self._setup_edge()
        elif preference == "openai-tts":
            ok, reason, message = self._setup_openai_tts()
        elif preference == "melotts":
            ok, reason, message = await self._setup_melotts()
        elif preference == "elevenlabs":
            ok, reason, message = await self._setup_elevenlabs()
        elif preference == "webspeech":
            # Sin backend: el navegador (Electron/Chromium) sintetiza con speechSynthesis.
            self._tts_engine = "webspeech"
            ok, reason, message = True, "ready", "TTS del navegador (Web Speech) listo."
        elif preference in GOOGLE_TTS_ENGINES:
            ok, reason, message = await self._setup_google(preference)
        else:
            ok, reason, message = False, "unsupported_model", "Motor TTS no soportado."

        logger.info(
            "VoiceEngine._init_tts provider result: "
            f"preference={preference}, "
            f"provider={provider}, "
            f"ok={ok}, "
            f"reason={reason}, "
            f"message={message}"
        )

        if ok:
            self._set_tts_status(
                requested_engine=preference,
                active_engine=self._tts_engine,
                available=True,
                reason="ready",
                message=message,
                warnings=warnings,
            )
            logger.info(
                "VoiceEngine._init_tts ready: "
                f"requested_engine={preference}, active_engine={self._tts_engine}"
            )
            return

        self._set_tts_status(
            requested_engine=preference,
            active_engine="none",
            available=False,
            reason=reason or "init_error",
            message=message,
            warnings=warnings,
        )
        logger.warning(
            "VoiceEngine._init_tts unavailable: "
            f"requested_engine={preference}, reason={reason}, message={message}"
        )

    def _setup_edge(self) -> tuple[bool, str, str]:
        if not tts_engines.HAS_EDGE_TTS:
            return False, "missing_dependency", "Falta el paquete edge-tts (pip install edge-tts)."
        self._tts_engine = "edge"
        return True, "ready", "Voces de Edge listas."

    def _setup_openai_tts(self) -> tuple[bool, str, str]:
        if not config.get_api_key("openai_api"):
            return False, "missing_key", "Falta la API key de OpenAI para usar este motor."
        self._tts_engine = "openai-tts"
        return True, "ready", "OpenAI TTS listo."

    async def _setup_melotts(self) -> tuple[bool, str, str]:
        if not HAS_MELOTTS:
            return False, "missing_dependency", "MeloTTS no esta instalado."

        try:
            lang = config.get("voice", "melotts_language", default="ES")
            device = config.get("voice", "melotts_device", default="auto")

            def _load():
                from melo.api import TTS as MeloTTSModel

                return MeloTTSModel(language=lang, device=device)

            self._melo_model = await asyncio.to_thread(_load)
            self._melo_speaker_ids = dict(self._melo_model.hps.data.spk2id.items())
            self._melo_language = lang
            self._tts_engine = "melotts"
            logger.info(
                f"TTS: MeloTTS inicializado (lang={lang}, speakers={list(self._melo_speaker_ids.keys())})"
            )
            return True, "ready", "MeloTTS listo."
        except Exception as exc:
            logger.warning(f"MeloTTS no disponible: {exc}")
            return False, "init_error", f"MeloTTS no pudo inicializarse: {exc}"

    async def _setup_elevenlabs(self) -> tuple[bool, str, str]:
        if not HAS_ELEVENLABS:
            return False, "missing_dependency", "El SDK de ElevenLabs no esta instalado."

        api_key = config.get_api_key("elevenlabs_api")
        if not api_key:
            return (
                False,
                "missing_key",
                "Falta la API key de ElevenLabs para usar este motor.",
            )

        try:
            from elevenlabs import AsyncElevenLabs

            self._eleven_client = AsyncElevenLabs(api_key=api_key)
            self._tts_engine = "elevenlabs"
            logger.info("TTS: ElevenLabs inicializado")
            return True, "ready", "ElevenLabs listo."
        except Exception as exc:
            logger.warning(f"ElevenLabs no disponible: {exc}")
            return False, "init_error", f"ElevenLabs no pudo inicializarse: {exc}"

    async def _setup_google(self, model: str) -> tuple[bool, str, str]:
        # Vertex AI si el chat usa Vertex o el provider Google está en modo Vertex.
        google_backend = config.get("providers", "google", "backend", default="ai_studio")
        chat_provider = str(config.get("model_router", "default_provider", default="") or "")
        if google_backend == "vertex_ai" or chat_provider == "vertex" or not config.get_api_key("google_api"):
            return await self._setup_google_vertex(model)

        # AI Studio: usa API key
        api_key = config.get_api_key("google_api")
        api_key_configured = bool(str(api_key or "").strip())
        logger.info(
            "VoiceEngine._setup_google start: "
            f"requested_model={model}, api_key_configured={api_key_configured}, backend=ai_studio"
        )
        if not api_key:
            logger.warning(
                "VoiceEngine._setup_google missing Google API key: "
                f"requested_model={model}"
            )
            return False, "missing_key", "Falta la API key de Google para usar Gemini TTS."

        try:
            from google import genai
            self._google_client = genai.Client(api_key=api_key)
            self._tts_engine = model
            logger.info(
                "VoiceEngine._setup_google success: "
                f"requested_model={model}, active_engine={self._tts_engine}, "
                f"api_key_configured={api_key_configured}, backend=ai_studio"
            )
            return True, "ready", f"Google TTS listo ({model})."
        except Exception as exc:
            logger.warning(
                "VoiceEngine._setup_google failed: "
                f"requested_model={model}, api_key_configured={api_key_configured}, error={exc}"
            )
            return False, "init_error", f"Google TTS no pudo inicializarse: {exc}"

    async def _setup_google_vertex(self, model: str) -> tuple[bool, str, str]:
        """Google TTS vía Vertex AI con la misma cuenta que el proveedor vertex.
        Los modelos TTS 3.8 solo están en la ubicación global (en us-central1 dan 404)."""
        try:
            from google import genai
            from google.genai import types

            from backend.providers import gcp_auth

            section = config.get("providers", "vertex", default={}) or {}
            settings = await asyncio.to_thread(gcp_auth.resolve_vertex_settings, section)
            if not settings.project:
                return False, "missing_project", (
                    "Vertex AI necesita un proyecto: inicia sesión con gcloud o configura una cuenta de servicio."
                )
            location = str(config.get("voice", "vertex_location", default="") or "global")
            kwargs: dict[str, Any] = {
                "vertexai": True, "project": settings.project, "location": location,
                "http_options": types.HttpOptions(timeout=60_000),
            }
            credentials = gcp_auth.load_credentials(settings.credentials_file)
            if credentials is not None:
                kwargs["credentials"] = credentials
            self._google_client = genai.Client(**kwargs)
            self._tts_engine = model
            logger.info(f"Google TTS vía Vertex: {model} (project={settings.project}, location={location})")
            return True, "ready", f"Google TTS listo ({model}, Vertex AI)."
        except Exception as exc:
            logger.warning(f"Google TTS (Vertex) no pudo inicializarse: {exc}")
            return False, "init_error", f"Google TTS (Vertex AI) no pudo inicializarse: {exc}"

    async def _init_stt(self) -> None:
        """Inicializa Faster-Whisper para STT."""
        _lazy_load_whisper()
        if not HAS_WHISPER:
            logger.info("STT: faster-whisper no disponible")
            return

        try:
            model_size = config.get("voice", "whisper_model", default=None) or config.get("voice", "stt_model", default="base")
            device = config.get("voice", "whisper_device", default="cpu")
            compute_type = config.get("voice", "whisper_compute", default="int8")

            loop = asyncio.get_running_loop()
            self._stt_model = await loop.run_in_executor(
                None,
                lambda: _WhisperModel(model_size, device=device, compute_type=compute_type),
            )
            logger.info(f"STT: Whisper ({model_size}) inicializado en {device}")
        except Exception as exc:
            logger.warning(f"STT Whisper no disponible: {exc}")

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        speed: float | None = None,
    ) -> bytes | None:
        """
        Sintetiza texto a audio WAV. Usa cache en memoria para frases repetidas.
        Retorna bytes del audio o None.
        """
        import hashlib

        if self._tts_loading is not None and not self._tts_loading.done():
            await self._tts_loading

        supports_numeric_speed = self._tts_runtime_status.get("supports_numeric_speed", False)
        effective_speed = float(speed if speed is not None else config.get("voice", "tts_speed", default=1.0))
        if not supports_numeric_speed:
            effective_speed = 1.0

        # La clave incluye la voz efectiva: antes cambiar de voz seguía repitiendo la anterior.
        resolved_voice = self._resolve_voice(voice_id)
        cache_key = hashlib.md5(
            f"{text}|{self._tts_engine}|{resolved_voice}|{effective_speed}".encode()
        ).hexdigest()

        if cache_key in self._tts_cache:
            logger.debug(f"TTS cache hit: {text[:40]}...")
            return self._tts_cache[cache_key]

        if self._tts_engine == "webspeech":
            return None  # el navegador habla el texto; no hay audio de servidor

        result: bytes | None = None
        if self._tts_engine == "edge":
            result = await self._tts_edge(text, resolved_voice, effective_speed)
        elif self._tts_engine == "openai-tts":
            result = await self._tts_openai(text, resolved_voice, effective_speed)
        elif self._tts_engine == "melotts":
            result = await self._tts_melo(text, effective_speed)
        elif self._tts_engine == "elevenlabs":
            result = await self._tts_elevenlabs(text, resolved_voice, effective_speed)
        elif self._tts_engine in GOOGLE_TTS_ENGINES:
            result = await self._tts_google(text, resolved_voice)
        else:
            logger.warning("No hay motor TTS disponible")
            return None

        if result and len(result) < 5_000_000:
            if len(self._tts_cache) >= self._TTS_CACHE_MAX:
                oldest_key = next(iter(self._tts_cache))
                del self._tts_cache[oldest_key]
            self._tts_cache[cache_key] = result

        return result

    def _resolve_voice(self, voice_id: str | None) -> str:
        if voice_id:
            return str(voice_id)
        engine = self._tts_engine
        if engine == "edge":
            return str(config.get("voice", "edge_voice", default="") or DEFAULT_EDGE_VOICE)
        if engine == "openai-tts":
            return str(config.get("voice", "openai_voice", default="") or tts_engines.DEFAULT_OPENAI_VOICE)
        if engine == "elevenlabs":
            return str(config.get("voice", "elevenlabs_voice_id", default="") or DEFAULT_ELEVENLABS_VOICE_ID)
        if engine in GOOGLE_TTS_ENGINES:
            return str(config.get("voice", "google_voice", default="") or DEFAULT_GOOGLE_VOICE)
        return ""

    async def _tts_edge(self, text: str, voice: str, speed: float) -> bytes | None:
        try:
            return await tts_engines.synthesize_edge(text, voice, speed=speed)
        except Exception as exc:
            logger.error(f"Edge TTS error: {exc}")
            return None

    async def _tts_openai(self, text: str, voice: str, speed: float) -> bytes | None:
        try:
            return await tts_engines.synthesize_openai(
                text,
                api_key=config.get_api_key("openai_api") or "",
                voice=voice,
                model=str(config.get("voice", "openai_tts_model", default="") or tts_engines.DEFAULT_OPENAI_TTS_MODEL),
                instructions=str(config.get("voice", "openai_tts_instructions", default="") or ""),
                speed=speed,
            )
        except Exception as exc:
            logger.error(f"OpenAI TTS error: {exc}")
            return None

    async def _tts_melo(self, text: str, speed: float = 1.0) -> bytes | None:
        """TTS con MeloTTS (offline)."""
        try:
            loop = asyncio.get_running_loop()

            def _generate() -> bytes:
                import os
                import tempfile

                speaker_id = self._melo_speaker_ids.get(
                    self._melo_language,
                    next(iter(self._melo_speaker_ids.values())),
                )
                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                tmp_path = tmp.name
                tmp.close()
                try:
                    self._melo_model.tts_to_file(text, speaker_id, tmp_path, speed=speed)
                    with open(tmp_path, "rb") as f:
                        return f.read()
                finally:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)

            return await loop.run_in_executor(None, _generate)
        except Exception as exc:
            logger.error(f"MeloTTS error: {exc}")
            return None

    def _elevenlabs_stream(self, text: str, voice_id: str, speed: float = 1.0):
        """convert() devuelve un iterador asíncrono: hacerle await (como antes)
        lanzaba TypeError y ElevenLabs nunca producía audio."""
        kwargs: dict[str, Any] = {
            "voice_id": voice_id,
            "text": text,
            "model_id": str(config.get("voice", "elevenlabs", "model", default="") or "eleven_multilingual_v2"),
            "output_format": "pcm_24000",
        }
        if abs(float(speed) - 1.0) > 0.01:
            kwargs["voice_settings"] = {"speed": max(0.7, min(1.2, float(speed)))}
        return self._eleven_client.text_to_speech.convert(**kwargs)

    async def _tts_elevenlabs(self, text: str, voice_id: str, speed: float = 1.0) -> bytes | None:
        """TTS con ElevenLabs (online). PCM 24 kHz envuelto en WAV."""
        try:
            chunks = [chunk async for chunk in self._elevenlabs_stream(text, voice_id, speed)]
            return _wrap_pcm16_as_wav(b"".join(chunks), sample_rate=24000) if chunks else None
        except Exception as exc:
            logger.error(f"ElevenLabs error: {exc}")
            return None

    async def _tts_google(self, text: str, voice_id: str | None = None) -> bytes | None:
        """TTS con Google Gemini — usa la API async nativa."""
        try:
            from google.genai import types

            # Voz: prioridad voice_id (runtime) > config > default
            voice_name = (
                voice_id
                or str(config.get("voice", "google_voice", default="") or "").strip()
                or DEFAULT_GOOGLE_VOICE
            )

            # Usar el cliente async para evitar asyncio.to_thread y el 400 INVALID_ARGUMENT
            # que ocurre cuando el SDK sincrónico infiere respuesta de texto en el thread pool.
            response = await self._google_client.aio.models.generate_content(
                model=self._tts_engine,
                contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice_name,
                            )
                        )
                    ),
                ),
            )

            candidate = response.candidates[0]
            part = candidate.content.parts[0]
            inline_data = getattr(part, "inline_data", None)
            if inline_data is None:
                raise RuntimeError("La respuesta de Google TTS no incluyo audio inline.")

            audio_bytes = inline_data.data
            if isinstance(audio_bytes, str):
                audio_bytes = base64.b64decode(audio_bytes)

            if audio_bytes[:4] == b"RIFF":
                return audio_bytes

            mime_type = str(getattr(inline_data, "mime_type", "") or "")
            sample_rate = _extract_sample_rate(mime_type, default=24000)
            return _wrap_pcm16_as_wav(audio_bytes, sample_rate=sample_rate)
        except Exception as exc:
            logger.error(f"Google TTS error: {exc}")
            return None

    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        """TTS streaming - genera chunks de audio progresivamente."""
        if self._tts_engine == "elevenlabs" and self._eleven_client:
            try:
                async for chunk in self._elevenlabs_stream(text, self._resolve_voice(None)):
                    yield chunk  # PCM16 24 kHz
            except Exception as exc:
                logger.error(f"ElevenLabs streaming error: {exc}")
        else:
            audio = await self.synthesize(text)
            if audio:
                yield audio

    async def transcribe(self, audio_bytes: bytes, prompt: str | None = None) -> str:
        """
        Transcribe audio a texto.
        Acepta audio WAV/MP3/OGG bytes. prompt orienta a Whisper con palabras que
        no conoce (por ejemplo el nombre del agente: "G-Mini" sale como "hemi ni").
        """
        if self._stt_loading is not None and not self._stt_loading.done():
            await self._stt_loading  # el primer uso espera la carga en segundo plano
        if not self._stt_model:
            logger.warning("STT no disponible")
            return ""

        try:
            loop = asyncio.get_running_loop()

            def _transcribe() -> str:
                buf = _audio_for_whisper(audio_bytes)
                language = str(config.get("voice", "stt_language", default="es") or "es").strip().lower()
                segments, _info = self._stt_model.transcribe(
                    buf,
                    language=None if language == "auto" else language,
                    beam_size=5,
                    vad_filter=True,
                    initial_prompt=prompt or None,
                )
                return " ".join([segment.text.strip() for segment in segments])

            text = await loop.run_in_executor(None, _transcribe)
            logger.debug(f"STT resultado: {text[:80]}...")
            return text
        except Exception as exc:
            logger.error(f"STT error: {exc}")
            return ""

    def generate_lipsync_data(self, audio_bytes: bytes) -> list[dict]:
        """
        Genera datos de lipsync para animacion del personaje.
        Usa analisis RMS del audio para detectar energia vocal real.
        """
        import math
        import struct

        sample_rate = 22050
        bytes_per_sample = 2
        frame_duration = 0.06
        raw = audio_bytes

        if len(raw) > 44 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
            fmt_offset = raw.find(b"fmt ")
            if fmt_offset >= 0 and fmt_offset + 16 <= len(raw):
                try:
                    sample_rate = struct.unpack_from("<I", raw, fmt_offset + 12)[0]
                except struct.error:
                    sample_rate = 22050

            data_offset = raw.find(b"data")
            if data_offset >= 0 and data_offset + 8 <= len(raw):
                try:
                    data_size = struct.unpack_from("<I", raw, data_offset + 4)[0]
                    raw = raw[data_offset + 8:data_offset + 8 + data_size]
                except struct.error:
                    raw = raw[44:]

        samples_per_frame = max(1, int(sample_rate * frame_duration))
        bytes_per_frame = samples_per_frame * bytes_per_sample
        total_frames = max(1, len(raw) // bytes_per_frame)
        visemes: list[dict] = []

        energy_visemes = ["rest", "A", "E", "O", "I", "U"]
        rms_values: list[float] = []

        for frame_idx in range(total_frames):
            offset = frame_idx * bytes_per_frame
            chunk = raw[offset:offset + bytes_per_frame]
            if len(chunk) < bytes_per_sample:
                break

            num_samples = len(chunk) // bytes_per_sample
            samples = struct.unpack(
                f"<{num_samples}h", chunk[: num_samples * bytes_per_sample]
            )
            sum_sq = sum(sample * sample for sample in samples)
            rms = math.sqrt(sum_sq / num_samples) if num_samples > 0 else 0.0
            rms_values.append(rms)

        if not rms_values:
            return [{"time": 0.0, "viseme": "rest", "weight": 0.0}]

        max_rms = max(rms_values) if max(rms_values) > 0 else 1.0
        silence_threshold = 0.05

        for frame_idx, rms in enumerate(rms_values):
            timestamp = round(frame_idx * frame_duration, 3)
            normalized = rms / max_rms

            if normalized < silence_threshold:
                viseme = "rest"
                weight = 0.0
            else:
                index = min(
                    int(normalized * (len(energy_visemes) - 1)),
                    len(energy_visemes) - 1,
                )
                viseme = energy_visemes[index]
                weight = round(min(normalized * 1.2, 1.0), 2)

            visemes.append(
                {
                    "time": timestamp,
                    "viseme": viseme,
                    "weight": weight,
                }
            )

        return visemes

    async def preview(self, engine_id: str, voice: str | None, text: str) -> bytes | None:
        """Muestra de una voz sin cambiar la configuración (selector con "Escuchar")."""
        engine_id, _ = normalize_tts_engine(engine_id)
        if engine_id == self._tts_engine:
            return await self.synthesize(text, voice_id=voice or None)
        if engine_id == "edge":
            return await tts_engines.synthesize_edge(text, voice or DEFAULT_EDGE_VOICE)
        if engine_id == "openai-tts":
            return await tts_engines.synthesize_openai(
                text, api_key=config.get_api_key("openai_api") or "", voice=voice or None
            )
        if engine_id in GOOGLE_TTS_ENGINES or engine_id == "elevenlabs":
            temp = VoiceEngine()
            ok, _reason, message = await (
                temp._setup_elevenlabs() if engine_id == "elevenlabs" else temp._setup_google(engine_id)
            )
            if not ok:
                raise RuntimeError(message)
            return await temp.synthesize(text, voice_id=voice or None)
        raise RuntimeError("Este motor no tiene vista previa desde el backend.")

    @property
    def tts_available(self) -> bool:
        return self._tts_engine != "none"

    @property
    def stt_available(self) -> bool:
        return self._stt_model is not None

    @property
    def tts_engine_name(self) -> str:
        return self._tts_engine

    @property
    def tts_is_browser(self) -> bool:
        """True si el TTS lo hace el navegador (Web Speech), no el backend.
        Los callers deben emitir el texto a hablar en vez de sintetizar audio."""
        return self._tts_engine == "webspeech"

    @property
    def tts_output_format(self) -> str:
        return "wav"

    @property
    def requested_tts_engine(self) -> str:
        return self._requested_tts_engine

    def get_tts_runtime_status(self) -> dict[str, Any]:
        return deepcopy(self._tts_runtime_status)
