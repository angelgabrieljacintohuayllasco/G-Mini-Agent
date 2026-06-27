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

HAS_MELOTTS = False
HAS_ELEVENLABS = False

try:
    from melo.api import TTS as MeloTTSModel

    HAS_MELOTTS = True
except ImportError:
    pass

try:
    from elevenlabs import AsyncElevenLabs

    HAS_ELEVENLABS = True
except ImportError:
    pass

DEFAULT_TTS_ENGINE = "melotts"
DEFAULT_GOOGLE_TTS_ENGINE = "gemini-2.5-flash-preview-tts"
DEFAULT_ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
DEFAULT_GOOGLE_VOICE = "Kore"

# Voces disponibles en Google Gemini TTS (https://ai.google.dev/gemini-api/docs/speech-generation)
GOOGLE_VOICE_CATALOG: list[dict] = [
    {"id": "Kore",             "description": "Firme"},
    {"id": "Puck",             "description": "Animado"},
    {"id": "Charon",          "description": "Informativo"},
    {"id": "Aoede",           "description": "Fluido"},
    {"id": "Zephyr",          "description": "Brillante"},
    {"id": "Fenrir",          "description": "Enérgico"},
    {"id": "Leda",            "description": "Juvenil"},
    {"id": "Orus",            "description": "Firme"},
    {"id": "Callirrhoe",      "description": "Tranquilo"},
    {"id": "Autonoe",         "description": "Brillante"},
    {"id": "Enceladus",       "description": "Suave"},
    {"id": "Iapetus",         "description": "Claro"},
    {"id": "Umbriel",         "description": "Tranquilo"},
    {"id": "Achernar",        "description": "Suave"},
    {"id": "Alnilam",         "description": "Firme"},
    {"id": "Schedar",         "description": "Equilibrado"},
    {"id": "Sulafat",         "description": "Cálido"},
    {"id": "Sadaltager",      "description": "Conocedor"},
    {"id": "Achird",          "description": "Amigable"},
]

TTS_ENGINE_CATALOG: dict[str, dict[str, Any]] = {
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
    "gemini-3.1-flash-tts-preview": {
        "label": "Gemini 3.1 Flash TTS (online)",
        "provider": "google",
        "online": True,
        "supports_numeric_speed": False,
    },
    "gemini-2.5-flash-preview-tts": {
        "label": "Gemini 2.5 Flash TTS (online)",
        "provider": "google",
        "online": True,
        "supports_numeric_speed": False,
    },
    "gemini-2.5-pro-preview-tts": {
        "label": "Gemini 2.5 Pro TTS (online)",
        "provider": "google",
        "online": True,
        "supports_numeric_speed": False,
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
    "gemini-2.5-pro-tts": "gemini-2.5-pro-preview-tts",
    "gemini-2.5-flash-tts": "gemini-2.5-flash-preview-tts",
    "gemini-2.5-flash-lite-preview-tts": DEFAULT_GOOGLE_TTS_ENGINE,
    "chirp_3": DEFAULT_GOOGLE_TTS_ENGINE,
    "chirp_2": DEFAULT_GOOGLE_TTS_ENGINE,
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
        self._set_tts_status(
            requested_engine="none",
            active_engine="none",
            available=False,
            reason="not_initialized",
            message="VoiceEngine no inicializado.",
        )

    async def initialize(self) -> None:
        """Inicializa los motores de voz configurados."""
        stt_enabled = bool(config.get("voice", "stt_enabled", default=True))
        await self.reload(reload_stt=stt_enabled)
        logger.info(f"VoiceEngine inicializado (TTS: {self._tts_engine})")

    async def reload(self, *, reload_stt: bool = False) -> None:
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
        await self._init_tts(requested_engine, warnings=warnings)

        if reload_stt:
            self._stt_model = None
            if bool(config.get("voice", "stt_enabled", default=True)):
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

        if preference == "melotts":
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

    async def _setup_melotts(self) -> tuple[bool, str, str]:
        if not HAS_MELOTTS:
            return False, "missing_dependency", "MeloTTS no esta instalado."

        try:
            lang = config.get("voice", "melotts_language", default="ES")
            device = config.get("voice", "melotts_device", default="auto")
            loop = asyncio.get_running_loop()
            self._melo_model = await loop.run_in_executor(
                None,
                lambda: MeloTTSModel(language=lang, device=device),
            )
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
            self._eleven_client = AsyncElevenLabs(api_key=api_key)
            self._tts_engine = "elevenlabs"
            logger.info("TTS: ElevenLabs inicializado")
            return True, "ready", "ElevenLabs listo."
        except Exception as exc:
            logger.warning(f"ElevenLabs no disponible: {exc}")
            return False, "init_error", f"ElevenLabs no pudo inicializarse: {exc}"

    async def _setup_google(self, model: str) -> tuple[bool, str, str]:
        # Determinar backend: Vertex AI o AI Studio (hereda config del provider Google)
        google_backend = config.get("providers", "google", "backend", default="ai_studio")

        if google_backend == "vertex_ai":
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
        """Configura Google TTS via Vertex AI (misma config que el provider LLM)."""
        import os

        project_id = config.get("providers", "google", "project_id", default="")
        raw_location = config.get("providers", "google", "location", default="us-central1")
        # "global" no funciona para Vertex AI generative models — fallback a us-central1
        location = raw_location if raw_location != "global" else "us-central1"
        credentials_file = config.get("providers", "google", "credentials_file", default="")

        logger.info(
            "VoiceEngine._setup_google_vertex start: "
            f"requested_model={model}, project_id={project_id}, location={location}"
        )

        if not project_id:
            logger.warning(
                "VoiceEngine._setup_google_vertex missing project_id: "
                f"requested_model={model}"
            )
            return False, "missing_project", (
                "Vertex AI requiere project_id. Configúralo en Ajustes > Google > Project ID."
            )

        try:
            from google import genai

            if credentials_file:
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_file

            self._google_client = genai.Client(
                vertexai=True,
                project=project_id,
                location=location,
            )
            self._tts_engine = model
            logger.info(
                "VoiceEngine._setup_google_vertex success: "
                f"requested_model={model}, active_engine={self._tts_engine}, "
                f"project={project_id}, location={location}"
            )
            return True, "ready", f"Google TTS listo ({model}, Vertex AI)."
        except Exception as exc:
            logger.warning(
                "VoiceEngine._setup_google_vertex failed: "
                f"requested_model={model}, project={project_id}, error={exc}"
            )
            return False, "init_error", f"Google TTS (Vertex AI) no pudo inicializarse: {exc}"

    async def _init_stt(self) -> None:
        """Inicializa Faster-Whisper para STT."""
        _lazy_load_whisper()
        if not HAS_WHISPER:
            logger.info("STT: faster-whisper no disponible")
            return

        try:
            model_size = config.get("voice", "whisper_model", default="base")
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

        supports_numeric_speed = self._tts_runtime_status.get("supports_numeric_speed", False)
        effective_speed = float(speed if speed is not None else config.get("voice", "tts_speed", default=1.0))
        if not supports_numeric_speed:
            effective_speed = 1.0

        cache_key = hashlib.md5(
            f"{text}|{self._tts_engine}|{voice_id}|{effective_speed}".encode()
        ).hexdigest()

        if cache_key in self._tts_cache:
            logger.debug(f"TTS cache hit: {text[:40]}...")
            return self._tts_cache[cache_key]

        if self._tts_engine == "webspeech":
            return None  # el navegador habla el texto; no hay audio de servidor

        result: bytes | None = None
        if self._tts_engine == "melotts":
            result = await self._tts_melo(text, effective_speed)
        elif self._tts_engine == "elevenlabs":
            result = await self._tts_elevenlabs(text, voice_id)
        elif self._tts_engine in GOOGLE_TTS_ENGINES:
            result = await self._tts_google(text, voice_id)
        else:
            logger.warning("No hay motor TTS disponible")
            return None

        if result and len(result) < 5_000_000:
            if len(self._tts_cache) >= self._TTS_CACHE_MAX:
                oldest_key = next(iter(self._tts_cache))
                del self._tts_cache[oldest_key]
            self._tts_cache[cache_key] = result

        return result

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

    async def _tts_elevenlabs(self, text: str, voice_id: str | None = None) -> bytes | None:
        """TTS con ElevenLabs (online)."""
        try:
            resolved_voice_id = (
                voice_id
                or config.get("voice", "elevenlabs_voice_id", default="")
                or DEFAULT_ELEVENLABS_VOICE_ID
            )

            audio = await self._eleven_client.text_to_speech.convert(
                voice_id=resolved_voice_id,
                text=text,
                model_id="eleven_multilingual_v2",
                output_format="wav_24000",
            )

            chunks = []
            async for chunk in audio:
                chunks.append(chunk)
            return b"".join(chunks)
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
                voice_id = (
                    config.get("voice", "elevenlabs_voice_id", default="")
                    or DEFAULT_ELEVENLABS_VOICE_ID
                )
                audio = await self._eleven_client.text_to_speech.convert(
                    voice_id=voice_id,
                    text=text,
                    model_id="eleven_multilingual_v2",
                    output_format="wav_24000",
                )
                async for chunk in audio:
                    yield chunk
            except Exception as exc:
                logger.error(f"ElevenLabs streaming error: {exc}")
        else:
            audio = await self.synthesize(text)
            if audio:
                yield audio

    async def transcribe(self, audio_bytes: bytes) -> str:
        """
        Transcribe audio a texto.
        Acepta audio WAV/MP3/OGG bytes.
        """
        if not self._stt_model:
            logger.warning("STT no disponible")
            return ""

        try:
            loop = asyncio.get_running_loop()

            def _transcribe() -> str:
                buf = io.BytesIO(audio_bytes)
                segments, _info = self._stt_model.transcribe(
                    buf,
                    language="es",
                    beam_size=5,
                    vad_filter=True,
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
