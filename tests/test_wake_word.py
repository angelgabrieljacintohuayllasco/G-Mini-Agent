"""Palabra de activación: frases, tolerancia a errores de Whisper y detector con VAD."""

from __future__ import annotations

from array import array

from backend.voice.wake_word import WakeWordDetector, match_wake, wake_phrases

CHUNK = 1600  # 100 ms a 16 kHz


def _chunk(amplitude: int) -> bytes:
    return array("h", [amplitude, -amplitude] * (CHUNK // 2)).tobytes()


def test_phrases_for_the_default_name_and_a_custom_one():
    default = wake_phrases("G-Mini")
    assert "oye gemini" in default and "hey g-mini" in default
    custom = wake_phrases("Luna", ["despierta luna"])
    assert "oye luna" in custom and "despierta luna" in custom and "gemini" not in custom


def test_match_returns_the_request_after_the_wake_word():
    phrases = wake_phrases("G-Mini")
    assert match_wake("Oye Gemini, ¿qué hora es?", phrases) == ("oye gemini", "qué hora es")
    assert match_wake("G-Mini", phrases)[0] in ("g-mini", "gmini")
    assert match_wake("oye geminy pon música", phrases)[1] == "pon música"  # error de transcripción
    assert match_wake("me gusta gemini", phrases) is None  # tiene que empezar con la activación
    # Whisper sin contexto parte el nombre: "Oye, hemi ni, que hora es."
    phrase, rest = match_wake("Oye, hemi ni, que hora es.", phrases)
    assert phrase.startswith("oye ") and rest == "que hora es"
    assert match_wake("", phrases) is None


async def test_detector_transcribes_only_short_phrases():
    calls: list[bytes] = []

    async def transcribe(wav: bytes) -> str:
        calls.append(wav)
        return "Oye G-Mini abre el correo"

    detector = WakeWordDetector(transcribe, wake_phrases("G-Mini"))
    results = []
    for chunk in [_chunk(0)] * 20 + [_chunk(5000)] * 12 + [_chunk(0)] * 8:
        hit = await detector.feed(chunk)
        if hit:
            results.append(hit)
    assert len(calls) == 1 and calls[0][:4] == b"RIFF"
    assert results == [("oye g-mini", "abre el correo", "Oye G-Mini abre el correo")]

    # Una frase de más de 4 s no es una activación: no se manda a Whisper.
    for chunk in [_chunk(5000)] * 50 + [_chunk(0)] * 8:
        assert await detector.feed(chunk) is None
    assert len(calls) == 1


async def test_detector_passes_the_name_to_whisper():
    seen = {}

    async def transcribe(wav: bytes, prompt: str | None = None) -> str:
        seen["prompt"] = prompt
        return "Oye Luna, enciende la luz"

    detector = WakeWordDetector(transcribe, wake_phrases("Luna"), prompt="Oye Luna.")
    hit = await detector.check_clip(_chunk(4000) * 10)
    assert seen["prompt"] == "Oye Luna." and hit[:2] == ("oye luna", "enciende la luz")


def test_audio_is_decoded_for_whisper_without_pyav():
    import io

    import numpy as np
    import soundfile as sf

    from backend.voice.engine import _audio_for_whisper

    buf = io.BytesIO()
    sf.write(buf, np.zeros((8000, 2), dtype="float32"), 8000, format="WAV")  # 1 s estéreo a 8 kHz
    audio = _audio_for_whisper(buf.getvalue())
    assert audio.dtype == np.float32 and audio.ndim == 1 and len(audio) == 16000
    assert not isinstance(_audio_for_whisper(b"no es audio"), np.ndarray)  # queda para PyAV
