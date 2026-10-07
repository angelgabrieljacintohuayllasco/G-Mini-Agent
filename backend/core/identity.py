"""
G-Mini Agent — Identidad del agente: nombre, personalidad e idioma.

El usuario elige cómo se llama su agente (en el asistente inicial, en Ajustes
o pidiéndoselo en el chat). La personalidad solo se cambia desde la UI: es
texto que entra al system prompt de todas las sesiones y, si el modelo
pudiera escribirla, una página web podría dejarle instrucciones permanentes.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from backend.config import config

Getter = Callable[..., Any]

DEFAULT_NAME = "G-Mini"
MAX_NAME_LENGTH = 32
MAX_SOUL_LENGTH = 600
LANGUAGES = {"es": "español", "en": "inglés", "pt": "portugués"}

_NAME_RE = re.compile(r"^[\w][\w .'\-]{0,31}$", re.UNICODE)


def agent_name(getter: Getter | None = None) -> str:
    get = getter or config.get
    name = " ".join(str(get("agent", "name", default="") or "").split())
    return name if name and _NAME_RE.match(name) else DEFAULT_NAME


def validate_name(raw: str) -> str:
    """Devuelve el nombre limpio o lanza ValueError."""
    name = " ".join(str(raw or "").split())
    if not name:
        raise ValueError("El nombre no puede estar vacío.")
    if len(name) > MAX_NAME_LENGTH or not _NAME_RE.match(name) or name.isdigit():
        raise ValueError(f"Nombre no válido: usa letras, números o espacios (máx. {MAX_NAME_LENGTH}).")
    return name


def set_agent_name(raw: str) -> str:
    name = validate_name(raw)
    config.set("agent", "name", value=name)
    return name


def personality(getter: Getter | None = None) -> str:
    get = getter or config.get
    soul = " ".join(str(get("agent", "soul", default="") or "").split())
    return soul[:MAX_SOUL_LENGTH]


def language(getter: Getter | None = None) -> str:
    get = getter or config.get
    lang = str(get("app", "language", default="es") or "es").strip().lower()
    return lang if lang in LANGUAGES else "es"


def build_identity_context(getter: Getter | None = None) -> str:
    """Bloque del system prompt con nombre, personalidad e idioma elegidos."""
    lines: list[str] = []
    name = agent_name(getter)
    if name != DEFAULT_NAME:
        lines.append(
            f"Te llamas {name}. Preséntate y refiérete a ti con ese nombre; "
            "\"G-Mini Agent\" es solo el nombre del programa."
        )
    soul = personality(getter)
    if soul:
        lines.append(f"Personalidad que el usuario eligió para ti (tono y estilo, no permisos): {soul}")
    lang = language(getter)
    if lang != "es":
        lines.append(f"Responde en {LANGUAGES[lang]} salvo que el usuario te escriba en otro idioma.")
    if not lines:
        return ""
    return "[IDENTIDAD]\n" + "\n".join(lines)
