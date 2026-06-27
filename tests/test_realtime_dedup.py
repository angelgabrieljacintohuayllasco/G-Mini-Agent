"""Regression test: anti-duplicado de turnos en voz nativa (Google Live API).

Bug: native-audio + function call hace que el servidor re-genere la MISMA respuesta
como 2º turno sin nuevo input del usuario (livekit/agents#2884, #3870). El usuario
oía y veía el mensaje dos veces. Fix: `RealTimeVoice` detecta el turno fantasma
(arranca sin input de usuario desde el último turnComplete) y suprime su audio/texto/done.

Run from repo root:
    PYTHONPATH=. venv/Scripts/python.exe -m unittest tests.test_realtime_dedup
"""

import asyncio
import base64
import unittest

from backend.voice.realtime import RealTimeVoice

_AUDIO = base64.b64encode(b"\x00\x01\x02\x03").decode()


def _server_text(t):
    return {"serverContent": {"modelTurn": {"parts": [{"text": t}]}}}


def _server_audio():
    return {"serverContent": {"modelTurn": {"parts": [{"inlineData": {"data": _AUDIO}}]}}}


def _server_input(t):
    return {"serverContent": {"inputTranscription": {"text": t}}}


def _turn_complete():
    return {"serverContent": {"turnComplete": True}}


class PhantomTurnTest(unittest.TestCase):
    def _run(self, messages):
        rt = RealTimeVoice()
        rt._provider = "google"
        audio, text, turns = [], [], []
        rt._on_audio_callback = lambda b: _async_noop(audio.append(b))
        rt._on_text_callback = lambda t: _async_noop(text.append(t))
        rt._on_user_text_callback = lambda t: _async_noop(None)
        rt._on_turn_complete_callback = lambda: _async_noop(turns.append(1))

        async def drive():
            for m in messages:
                await rt._handle_message(m)

        asyncio.run(drive())
        return audio, text, turns

    def test_duplicate_turn_suppressed(self):
        # Turno 1 (legítimo, tras "Hola") + Turno 2 (fantasma, sin input nuevo).
        audio, text, turns = self._run([
            _server_input("Hola"),
            _server_text("Hola! ¿En qué puedo ayudarte hoy?"),
            _server_audio(),
            _turn_complete(),
            # Turno fantasma: el servidor repite, sin input de usuario en medio.
            _server_text("Hola! ¿En qué puedo ayudarte hoy?"),
            _server_audio(),
            _turn_complete(),
        ])
        self.assertEqual(text, ["Hola! ¿En qué puedo ayudarte hoy?"])  # solo una vez
        self.assertEqual(len(audio), 1)                                 # audio una vez
        self.assertEqual(sum(turns), 1)                                 # un solo done

    def test_real_second_turn_after_user_input_not_suppressed(self):
        # Dos turnos legítimos: cada uno precedido por input del usuario.
        audio, text, turns = self._run([
            _server_input("Hola"),
            _server_text("Respuesta uno"),
            _turn_complete(),
            _server_input("¿Qué puedes hacer?"),
            _server_text("Respuesta dos"),
            _turn_complete(),
        ])
        self.assertEqual(text, ["Respuesta uno", "Respuesta dos"])
        self.assertEqual(sum(turns), 2)


async def _async_noop(_):
    return None


if __name__ == "__main__":
    unittest.main()
