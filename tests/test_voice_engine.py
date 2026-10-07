"""Motor de voz: modelos vigentes, caché por voz, ElevenLabs, carga diferida y STT (sin red)."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from backend.voice import engine as voice_engine
from backend.voice.engine import VoiceEngine, normalize_tts_engine

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("legacy", [
    "gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts", "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-tts", "chirp_3",
])
def test_retired_gemini_tts_models_migrate(legacy):
    engine_id, warning = normalize_tts_engine(legacy)
    assert engine_id == "gemini-3.8-flash-tts" and warning


def test_importing_the_engine_does_not_load_torch():
    code = "import sys, backend.voice.engine; print('torch' in sys.modules, 'melo.api' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().splitlines()[-1] == "False False"


def _engine_with(tts: str) -> VoiceEngine:
    engine = VoiceEngine()
    engine._tts_engine = tts
    engine._set_tts_status(requested_engine=tts, active_engine=tts, available=True, reason="ready", message="")
    return engine


async def test_cache_key_includes_the_voice(monkeypatch):
    calls = []

    async def fake_edge(text, voice, *, speed=1.0, **kw):
        calls.append(voice)
        return b"RIFF" + voice.encode()

    monkeypatch.setattr(voice_engine.tts_engines, "synthesize_edge", fake_edge)
    engine = _engine_with("edge")
    a = await engine.synthesize("hola", voice_id="es-PE-CamilaNeural")
    b = await engine.synthesize("hola", voice_id="es-PE-AlexNeural")
    again = await engine.synthesize("hola", voice_id="es-PE-AlexNeural")
    assert a != b and again == b
    assert calls == ["es-PE-CamilaNeural", "es-PE-AlexNeural"]


async def test_elevenlabs_returns_wav_from_an_async_iterator():
    received = {}

    class _TTS:
        def convert(self, **kwargs):  # el SDK devuelve un iterador, no una corrutina
            received.update(kwargs)

            async def gen():
                yield b"\x01\x00" * 100
                yield b"\x02\x00" * 100

            return gen()

    class _Client:
        text_to_speech = _TTS()

    engine = _engine_with("elevenlabs")
    engine._eleven_client = _Client()
    audio = await engine._tts_elevenlabs("hola", "voz-1", speed=1.1)
    assert audio[:4] == b"RIFF" and len(audio) == 44 + 400
    assert received["output_format"] == "pcm_24000" and received["voice_settings"] == {"speed": 1.1}


async def test_heavy_engines_load_in_background(monkeypatch):
    gate = asyncio.Event()

    async def slow_init(self, preference, *, warnings=None):
        await gate.wait()
        self._tts_engine = "melotts"
        self._set_tts_status(requested_engine="melotts", active_engine="melotts", available=True,
                             reason="ready", message="ok")

    async def fake_melo(self, text, speed=1.0):
        return b"RIFFmelo"

    monkeypatch.setattr(VoiceEngine, "_init_tts", slow_init)
    monkeypatch.setattr(VoiceEngine, "_tts_melo", fake_melo)
    monkeypatch.setattr(voice_engine.config, "get", lambda *k, default=None: "melotts" if k == ("voice", "tts_primary") else default)
    monkeypatch.setattr(voice_engine, "migrate_voice_config", lambda: [])
    engine = VoiceEngine()
    await engine.reload(background=True)  # vuelve enseguida
    assert engine.get_tts_runtime_status()["reason"] == "loading"
    pending = asyncio.create_task(engine.synthesize("hola"))
    await asyncio.sleep(0.05)
    assert not pending.done()
    gate.set()
    assert await pending == b"RIFFmelo"


async def test_stt_uses_configured_language(monkeypatch):
    seen = {}

    class _Model:
        def transcribe(self, buf, **kwargs):
            seen.update(kwargs)

            class Seg:
                text = " hola "

            return [Seg()], None

    engine = VoiceEngine()
    engine._stt_model = _Model()
    monkeypatch.setattr(voice_engine.config, "get", lambda *k, default=None: "auto" if k == ("voice", "stt_language") else default)
    assert await engine.transcribe(b"x") == "hola"
    assert seen["language"] is None


async def test_preview_does_not_touch_the_active_engine(monkeypatch):
    async def fake_edge(text, voice, **kw):
        return b"RIFFpreview:" + voice.encode()

    monkeypatch.setattr(voice_engine.tts_engines, "synthesize_edge", fake_edge)
    engine = _engine_with("webspeech")
    audio = await engine.preview("edge", "es-PE-AlexNeural", "hola")
    assert audio == b"RIFFpreview:es-PE-AlexNeural" and engine.tts_engine_name == "webspeech"
    with pytest.raises(RuntimeError):
        await engine.preview("melotts", None, "hola")
