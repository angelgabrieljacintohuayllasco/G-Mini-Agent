"""Buscar un texto entre las líneas que devuelve el OCR y ubicarlo en pantalla."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalize(text: str) -> str:
    """Minúsculas, sin tildes ni puntuación de borde, espacios simples: 'Configuración:' -> 'configuracion'."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    plain = re.sub(r"[^\w\s@.\-/:]", " ", plain)
    plain = re.sub(r"\s+", " ", plain).strip(" .:-/")
    return plain


def find_text(lines: list[dict[str, Any]], query: str, *, exact: bool = False, limit: int = 10) -> list[dict[str, Any]]:
    """Coincidencias de query en las líneas OCR, con la caja que une sus palabras.

    lines: [{"text": str, "words": [{"text", "x", "y", "w", "h"}]}]. Con exact
    las palabras deben ser justo el texto buscado; si no, basta con contenerlo.
    Primero las coincidencias exactas, luego las más cortas.
    """
    target = normalize(query)
    if not target:
        return []
    found: list[tuple[int, int, dict[str, Any]]] = []
    for line in lines:
        words = [w for w in line.get("words") or [] if str(w.get("text", "")).strip()]
        for start in range(len(words)):
            joined = ""
            for end in range(start, len(words)):
                joined = normalize(f"{joined} {words[end]['text']}")
                hit = joined == target if exact else target in joined
                if not hit:
                    if len(joined) > len(target) + 40:
                        break
                    continue
                span = words[start:end + 1]
                left = min(w["x"] for w in span)
                top = min(w["y"] for w in span)
                right = max(w["x"] + w["w"] for w in span)
                bottom = max(w["y"] + w["h"] for w in span)
                found.append((0 if joined == target else 1, end - start, {
                    "text": " ".join(str(w["text"]) for w in span),
                    "line": line.get("text", ""),
                    "x": left, "y": top, "w": right - left, "h": bottom - top,
                }))
                break
    found.sort(key=lambda item: (item[0], item[1], item[2]["y"], item[2]["x"]))
    unique: list[dict[str, Any]] = []
    for _, _, match in found:
        box = (round(match["x"]), round(match["y"]), round(match["w"]), round(match["h"]))
        if any((round(m["x"]), round(m["y"]), round(m["w"]), round(m["h"])) == box for m in unique):
            continue
        unique.append(match)
        if len(unique) >= limit:
            break
    return unique
