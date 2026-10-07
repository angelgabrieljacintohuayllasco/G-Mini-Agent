"""Voz simulada: el detector de voz no acumula silencio ni se queda colgado con ruido constante."""

from __future__ import annotations

from array import array

from backend.voice.simulated_realtime import SimulatedRealtimeVoice

CHUNK_SAMPLES = 1600  # 100 ms a 16 kHz


def _chunk(amplitude: int) -> bytes:
    return array("h", [amplitude, -amplitude] * (CHUNK_SAMPLES // 2)).tobytes()


def _session(monkeypatch):
    vad = SimulatedRealtimeVoice()
    vad._active = True
    sent: list[bytes] = []

    async def fake_process(audio):
        sent.append(audio)

    monkeypatch.setattr(vad, "_process_utterance", fake_process)
    return vad, sent


async def _feed(vad, chunk, n, track=None):
    for _ in range(n):
        await vad.send_audio(chunk)
        if track is not None:
            track.append(len(vad._audio_buffer))
        if vad._process_task:  # deja correr la tarea falsa
            await vad._process_task
            vad._process_task = None


def test_rms_of_a_square_wave():
    assert round(SimulatedRealtimeVoice._calculate_rms(_chunk(1000))) == 1000
    assert SimulatedRealtimeVoice._calculate_rms(b"") == 0.0


async def test_leading_silence_keeps_only_the_pre_roll(monkeypatch):
    vad, sent = _session(monkeypatch)
    await _feed(vad, _chunk(0), 600)  # un minuto de silencio
    assert len(vad._audio_buffer) <= vad._ms_to_bytes(vad._PRE_ROLL_MS)
    assert sent == []


async def test_speech_then_silence_sends_one_utterance_with_pre_roll(monkeypatch):
    vad, sent = _session(monkeypatch)
    await _feed(vad, _chunk(0), 10)
    await _feed(vad, _chunk(4000), 15)
    await _feed(vad, _chunk(0), 15)
    assert len(sent) == 1
    seconds = len(sent[0]) / 2 / 16000
    assert 1.5 + 0.3 <= seconds + 1e-6 <= 1.5 + 0.3 + 1.3  # voz + pre-roll + silencio de cierre


async def test_constant_noise_never_grows_without_limit(monkeypatch):
    vad, sent = _session(monkeypatch)
    sizes: list[int] = []
    await _feed(vad, _chunk(700), 600, sizes)  # un minuto de ruido por encima del umbral fijo
    assert max(sizes) <= vad._ms_to_bytes(vad._MAX_UTTERANCE_MS)
    assert len(sent) <= 3  # se corta solo y luego lo trata como ruido de fondo
    await _feed(vad, _chunk(6000), 10)  # el habla sigue detectándose sobre ese ruido
    assert vad._has_speech
