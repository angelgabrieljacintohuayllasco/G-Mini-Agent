"""Extraccion de tags de emocion `[happy]`, `[sad]`, etc. emitidos por el LLM.

Los tags se quitan del texto antes de mostrarlo/hablarlo y se reenvian al
frontend como evento `agent:emotion` para animar la skin VRM.
"""

from __future__ import annotations

import re

EMOTIONS = {"happy", "sad", "angry", "surprised", "relaxed", "neutral"}

_TAG_RE = re.compile(r"\[(" + "|".join(EMOTIONS) + r")\]", re.IGNORECASE)
_ALL_TAGS = [f"[{e}]" for e in EMOTIONS]


def _is_partial_tag(suffix: str) -> bool:
    """True si `suffix` (que empieza con '[') podria ser el prefijo de un tag valido."""
    s = suffix.lower()
    return any(tag.startswith(s) for tag in _ALL_TAGS)


def extract_emotion_tags(text: str) -> tuple[str, str | None]:
    """Quita todos los tags de emocion de un texto completo.

    Devuelve (texto_limpio, ultima_emocion_encontrada_o_None).
    """
    matches = _TAG_RE.findall(text)
    clean = _TAG_RE.sub("", text)
    emotion = matches[-1].lower() if matches else None
    return clean.strip(), emotion


class EmotionTagFilter:
    """Filtro con estado para texto en streaming.

    Quita tags de emocion chunk a chunk, reteniendo el sufijo si podria ser
    un tag partido a la mitad (p.ej. un chunk que termina en `[hap`).
    """

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> tuple[str, str | None]:
        """Procesa un chunk nuevo.

        Devuelve (texto_limpio_para_emitir, emocion_detectada_o_None).
        Si se detecta mas de un tag en el chunk, se reporta el ultimo.
        """
        self._buffer += chunk
        emotion: str | None = None
        out: list[str] = []

        while True:
            match = _TAG_RE.search(self._buffer)
            if match:
                out.append(self._buffer[:match.start()])
                emotion = match.group(1).lower()
                self._buffer = self._buffer[match.end():]
                continue

            idx = self._buffer.rfind("[")
            if idx == -1:
                out.append(self._buffer)
                self._buffer = ""
            else:
                suffix = self._buffer[idx:]
                if _is_partial_tag(suffix):
                    out.append(self._buffer[:idx])
                    self._buffer = suffix
                else:
                    out.append(self._buffer)
                    self._buffer = ""
            break

        return "".join(out), emotion

    def flush(self) -> str:
        """Devuelve cualquier texto retenido al terminar el streaming."""
        rest = self._buffer
        self._buffer = ""
        return rest
