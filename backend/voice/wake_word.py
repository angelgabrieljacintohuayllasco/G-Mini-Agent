"""Palabra de activación: "Oye G-Mini" (o el nombre que le pusiste al agente).

La interfaz o un dispositivo manda el micrófono en trozos PCM16 16 kHz mono.
Un detector de voz por energía arma frases cortas y solo esas pasan por el
Whisper de la app. Si la frase empieza con la palabra de activación, avisa
con el resto de lo dicho ("oye g-mini, ¿qué hora es?" -> "¿qué hora es?").
"""

from __future__ import annotations

import difflib
import inspect
import io
import re
import wave
from typing import Awaitable, Callable

import numpy as np

from backend.vision.text_locate import normalize

SAMPLE_RATE = 16_000
# Cómo suele transcribir Whisper "G-Mini" cuando no conoce la palabra.
_GMINI_VARIANTS = ("g mini", "g-mini", "gmini", "gemini", "yemini", "jemini", "ge mini", "geminis", "jimini")
_PREFIXES = ("", "oye ", "hey ", "ok ", "okey ", "hola ")

Transcriber = Callable[[bytes], Awaitable[str]]


def wake_phrases(agent_name: str = "G-Mini", extra: list[str] | None = None) -> list[str]:
    names = {normalize(agent_name)} if agent_name else set()
    if not names or normalize(agent_name) in {normalize(v) for v in _GMINI_VARIANTS}:
        names |= {normalize(v) for v in _GMINI_VARIANTS}
    phrases = {f"{prefix}{name}".strip() for name in names if name for prefix in _PREFIXES}
    phrases |= {normalize(p) for p in (extra or []) if normalize(p)}
    return sorted(phrases, key=len, reverse=True)


def match_wake(transcript: str, phrases: list[str]) -> tuple[str, str] | None:
    """(frase detectada, resto de lo dicho) si el texto empieza con la palabra de activación."""
    text = normalize(transcript)
    if not text:
        return None
    words = text.split()
    for phrase in phrases:
        if text == phrase or text.startswith(phrase + " "):
            return phrase, _rest(transcript, len(phrase.split()))
    # Tolerancia a errores de transcripción en las primeras palabras ("oye gemi ni").
    for size in (1, 2, 3):
        head = " ".join(words[:size])
        for phrase in phrases:
            if len(phrase.split()) == size and difflib.SequenceMatcher(None, head, phrase).ratio() >= 0.82:
                return phrase, _rest(transcript, size)
    # Sin espacios: Whisper parte el nombre ("oye hemi ni" ~ "oyegemini").
    for phrase in phrases:
        target = phrase.replace(" ", "").replace("-", "")
        if len(target) < 5:
            continue
        for size in (1, 2, 3, 4):
            head = "".join(words[:size]).replace("-", "")
            if abs(len(head) - len(target)) <= 2 and difflib.SequenceMatcher(None, head, target).ratio() >= 0.8:
                return phrase, _rest(transcript, size)
    return None


def _rest(transcript: str, skip_words: int) -> str:
    original = re.sub(r"\s+", " ", str(transcript or "")).strip()
    parts = original.split(" ")
    rest = " ".join(parts[skip_words:]).strip(" ,.;:!¡?¿")
    return rest


def pcm16_to_wav(pcm16: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm16)
    return buf.getvalue()


class WakeWordDetector:
    """Arma frases con un VAD simple y transcribe solo las cortas."""

    SILENCE_RMS = 300
    END_SILENCE_MS = 600
    MIN_PHRASE_MS = 300
    MAX_PHRASE_MS = 4000  # una palabra de activación no dura más
    PRE_ROLL_MS = 300

    def __init__(self, transcribe: Transcriber, phrases: list[str], prompt: str = ""):
        self._transcribe = transcribe
        self.phrases = phrases
        self.prompt = prompt  # orienta a Whisper hacia el nombre del agente
        try:
            self._takes_prompt = "prompt" in inspect.signature(transcribe).parameters
        except (TypeError, ValueError):
            self._takes_prompt = False
        self._buffer = bytearray()
        self._speech = False
        self._silence_ms = 0.0
        self._noise_floor = 0.0
        self._too_long = False

    @staticmethod
    def _ms(pcm: bytes | bytearray) -> float:
        return len(pcm) / 2 / SAMPLE_RATE * 1000

    def _bytes(self, ms: int) -> int:
        return int(SAMPLE_RATE * ms / 1000) * 2

    async def feed(self, pcm16: bytes) -> tuple[str, str, str] | None:
        """Devuelve (frase, resto, transcripción) cuando detecta la palabra de activación."""
        if len(pcm16) < 2:
            return None
        samples = np.frombuffer(pcm16[: len(pcm16) // 2 * 2], dtype="<i2").astype(np.float32)
        rms = float(np.sqrt(np.mean(samples * samples)))
        is_speech = rms > max(self.SILENCE_RMS, self._noise_floor * 2.5)
        self._noise_floor += (0.2 if rms < self._noise_floor else 0.01) * (rms - self._noise_floor)
        self._buffer.extend(pcm16)

        if not self._speech and not is_speech:
            keep = self._bytes(self.PRE_ROLL_MS)
            if len(self._buffer) > keep:
                del self._buffer[:-keep]
            return None
        if is_speech:
            self._speech = True
            self._silence_ms = 0.0
        else:
            self._silence_ms += self._ms(pcm16)
        if self._ms(self._buffer) > self.MAX_PHRASE_MS:
            self._too_long = True  # frase larga: no es una activación; se descarta al terminar
            del self._buffer[: len(self._buffer) - self._bytes(self.PRE_ROLL_MS)]
        if self._speech and self._silence_ms >= self.END_SILENCE_MS:
            phrase_audio = bytes(self._buffer)
            too_long = self._too_long
            self._buffer.clear()
            self._speech = False
            self._silence_ms = 0.0
            self._too_long = False
            if too_long or self._ms(phrase_audio) < self.MIN_PHRASE_MS:
                return None
            return await self.check_clip(phrase_audio)
        return None

    async def check_clip(self, pcm16: bytes) -> tuple[str, str, str] | None:
        wav = pcm16_to_wav(pcm16)
        transcript = (await (self._transcribe(wav, prompt=self.prompt) if self._takes_prompt
                             else self._transcribe(wav))).strip()
        hit = match_wake(transcript, self.phrases)
        return (hit[0], hit[1], transcript) if hit else None
