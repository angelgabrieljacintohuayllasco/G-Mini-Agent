"""
G-Mini Agent — Motores TTS adicionales y catálogos de voces.

- Edge TTS: voces neuronales de Microsoft (gratis, sin API key, requiere internet).
- OpenAI TTS: gpt-4o-mini-tts con instrucciones de estilo.
- Catálogo completo de voces de Gemini TTS.

Todas las funciones de síntesis devuelven WAV PCM16 mono (lo que espera el
VoiceEngine para reproducir y calcular el lipsync).
"""

from __future__ import annotations

import asyncio
import io
import time
from typing import Any

from loguru import logger

try:
    import edge_tts

    HAS_EDGE_TTS = True
except ImportError:  # pragma: no cover - depende del entorno
    edge_tts = None  # type: ignore[assignment]
    HAS_EDGE_TTS = False

DEFAULT_EDGE_VOICE = "es-MX-DaliaNeural"
DEFAULT_OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_OPENAI_VOICE = "coral"

# Voces prediseñadas de Gemini TTS (30). Descripción según la guía oficial de estilos.
GEMINI_VOICES: list[dict[str, str]] = [
    {"id": "Zephyr", "description": "Brillante", "gender": "female"},
    {"id": "Puck", "description": "Animada", "gender": "male"},
    {"id": "Charon", "description": "Informativa", "gender": "male"},
    {"id": "Kore", "description": "Firme", "gender": "female"},
    {"id": "Fenrir", "description": "Entusiasta", "gender": "male"},
    {"id": "Leda", "description": "Juvenil", "gender": "female"},
    {"id": "Orus", "description": "Firme", "gender": "male"},
    {"id": "Aoede", "description": "Ligera", "gender": "female"},
    {"id": "Callirrhoe", "description": "Relajada", "gender": "female"},
    {"id": "Autonoe", "description": "Brillante", "gender": "female"},
    {"id": "Enceladus", "description": "Susurrante", "gender": "male"},
    {"id": "Iapetus", "description": "Clara", "gender": "male"},
    {"id": "Umbriel", "description": "Tranquila", "gender": "male"},
    {"id": "Algieba", "description": "Suave", "gender": "male"},
    {"id": "Despina", "description": "Suave", "gender": "female"},
    {"id": "Erinome", "description": "Clara", "gender": "female"},
    {"id": "Algenib", "description": "Grave", "gender": "male"},
    {"id": "Rasalgethi", "description": "Informativa", "gender": "male"},
    {"id": "Laomedeia", "description": "Alegre", "gender": "female"},
    {"id": "Achernar", "description": "Suave", "gender": "female"},
    {"id": "Alnilam", "description": "Firme", "gender": "male"},
    {"id": "Schedar", "description": "Equilibrada", "gender": "male"},
    {"id": "Gacrux", "description": "Madura", "gender": "female"},
    {"id": "Pulcherrima", "description": "Directa", "gender": "female"},
    {"id": "Achird", "description": "Amigable", "gender": "male"},
    {"id": "Zubenelgenubi", "description": "Casual", "gender": "male"},
    {"id": "Vindemiatrix", "description": "Delicada", "gender": "female"},
    {"id": "Sadachbia", "description": "Vivaz", "gender": "male"},
    {"id": "Sadaltager", "description": "Experta", "gender": "male"},
    {"id": "Sulafat", "description": "Cálida", "gender": "female"},
]

# Voces de OpenAI TTS (gpt-4o-mini-tts).
OPENAI_VOICES: list[dict[str, str]] = [
    {"id": "alloy", "description": "Neutra"},
    {"id": "ash", "description": "Seria"},
    {"id": "ballad", "description": "Melódica"},
    {"id": "coral", "description": "Cálida"},
    {"id": "echo", "description": "Resonante"},
    {"id": "fable", "description": "Narradora"},
    {"id": "nova", "description": "Enérgica"},
    {"id": "onyx", "description": "Grave"},
    {"id": "sage", "description": "Serena"},
    {"id": "shimmer", "description": "Brillante"},
    {"id": "verse", "description": "Expresiva"},
    {"id": "marin", "description": "Natural"},
    {"id": "cedar", "description": "Natural grave"},
]

_edge_voices_cache: tuple[float, list[dict[str, Any]]] | None = None
_EDGE_CACHE_TTL = 6 * 3600.0


def mp3_to_wav(audio: bytes) -> bytes:
    """Decodifica MP3 (u otro formato soportado por libsndfile) a WAV PCM16."""
    import soundfile as sf

    data, sample_rate = sf.read(io.BytesIO(audio), dtype="int16")
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1).astype("int16")
    out = io.BytesIO()
    sf.write(out, data, sample_rate, format="WAV", subtype="PCM_16")
    return out.getvalue()


def _signed_percent(value: float) -> str:
    """1.15 -> '+15%' (formato de rate/volume de Edge TTS)."""
    pct = int(round((float(value) - 1.0) * 100))
    pct = max(-90, min(200, pct))
    return f"{pct:+d}%"


def _signed_hz(value: float) -> str:
    hz = int(round(float(value)))
    hz = max(-100, min(100, hz))
    return f"{hz:+d}Hz"


async def list_edge_voices(locale_prefix: str | None = None) -> list[dict[str, Any]]:
    """Voces de Edge TTS (cacheadas 6 h). locale_prefix='es' filtra español."""
    global _edge_voices_cache
    if not HAS_EDGE_TTS:
        return []
    now = time.monotonic()
    if _edge_voices_cache is None or now - _edge_voices_cache[0] > _EDGE_CACHE_TTL:
        raw = await asyncio.wait_for(edge_tts.list_voices(), timeout=20)
        voices = [
            {
                "id": v.get("ShortName", ""),
                "name": v.get("FriendlyName") or v.get("ShortName", ""),
                "locale": v.get("Locale", ""),
                "gender": str(v.get("Gender", "")).lower(),
                "styles": (v.get("VoiceTag") or {}).get("VoicePersonalities", []),
            }
            for v in raw
            if v.get("ShortName")
        ]
        voices.sort(key=lambda v: (v["locale"], v["id"]))
        _edge_voices_cache = (now, voices)
    voices = _edge_voices_cache[1]
    if locale_prefix:
        prefix = locale_prefix.lower()
        voices = [v for v in voices if v["locale"].lower().startswith(prefix)]
    return voices


async def synthesize_edge(
    text: str,
    voice: str | None = None,
    *,
    speed: float = 1.0,
    pitch_hz: float = 0.0,
    volume: float = 1.0,
    timeout: float = 45.0,
) -> bytes:
    """Sintetiza con Edge TTS y devuelve WAV."""
    if not HAS_EDGE_TTS:
        raise RuntimeError("Falta el paquete edge-tts (pip install edge-tts).")
    clean = (text or "").strip()
    if not clean:
        raise ValueError("Texto vacío.")
    communicate = edge_tts.Communicate(
        clean,
        voice or DEFAULT_EDGE_VOICE,
        rate=_signed_percent(speed),
        volume=_signed_percent(volume),
        pitch=_signed_hz(pitch_hz),
    )
    chunks: list[bytes] = []

    async def _collect() -> None:
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                chunks.append(chunk["data"])

    await asyncio.wait_for(_collect(), timeout=timeout)
    if not chunks:
        raise RuntimeError("Edge TTS no devolvió audio (¿voz inválida o sin conexión?).")
    mp3 = b"".join(chunks)
    return await asyncio.to_thread(mp3_to_wav, mp3)


async def synthesize_openai(
    text: str,
    *,
    api_key: str,
    voice: str | None = None,
    model: str | None = None,
    instructions: str = "",
    speed: float = 1.0,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> bytes:
    """Sintetiza con OpenAI TTS (WAV)."""
    from openai import AsyncOpenAI

    if not api_key:
        raise RuntimeError("Falta la API key de OpenAI.")
    client = AsyncOpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout)
    params: dict[str, Any] = {
        "model": model or DEFAULT_OPENAI_TTS_MODEL,
        "voice": voice or DEFAULT_OPENAI_VOICE,
        "input": text,
        "response_format": "wav",
    }
    if instructions:
        params["instructions"] = instructions
    if abs(float(speed) - 1.0) > 0.01:
        params["speed"] = max(0.25, min(4.0, float(speed)))
    try:
        response = await client.audio.speech.create(**params)
        return response.content if hasattr(response, "content") else bytes(response.read())
    finally:
        await client.close()


def voice_catalog(engine_provider: str) -> list[dict[str, Any]]:
    """Catálogo estático de voces para motores sin listado dinámico."""
    if engine_provider == "google":
        return [dict(v) for v in GEMINI_VOICES]
    if engine_provider == "openai":
        return [dict(v) for v in OPENAI_VOICES]
    return []


def log_engine_versions() -> None:
    if HAS_EDGE_TTS:
        logger.debug(f"edge-tts {getattr(edge_tts, '__version__', '?')} disponible")
