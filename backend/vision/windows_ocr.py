"""OCR que trae Windows 10 y 11 (Windows.Media.Ocr).

No necesita Tesseract ni descargar modelos: usa los idiomas de Windows del
usuario (español, inglés...) y devuelve cada palabra con su caja. Corre en
unos 100-200 ms por pantalla.
"""

from __future__ import annotations

import asyncio
import io
import sys
from typing import Any


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        from winrt.windows.media.ocr import OcrEngine

        return OcrEngine.try_create_from_user_profile_languages() is not None
    except Exception:
        return False


async def _recognize(png_bytes: bytes) -> list[dict[str, Any]]:
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png_bytes)
    await writer.store_async()
    writer.detach_stream()
    stream.seek(0)
    bitmap = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("Windows no tiene idiomas con OCR instalados (Configuración > Hora e idioma)")
    result = await engine.recognize_async(bitmap)
    lines = []
    for line in result.lines:
        words = []
        for word in line.words:
            rect = word.bounding_rect
            words.append({"text": word.text, "x": rect.x, "y": rect.y, "w": rect.width, "h": rect.height})
        lines.append({"text": line.text, "words": words})
    return lines


def recognize(image) -> list[dict[str, Any]]:
    """Líneas con sus palabras y cajas (píxeles de la imagen). Llamar desde un hilo, no desde el event loop."""
    from winrt.windows.media.ocr import OcrEngine

    image = image.convert("RGB")
    scale = 1.0
    limit = int(OcrEngine.max_image_dimension or 0)
    if limit and max(image.size) > limit:
        scale = limit / max(image.size)
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    lines = asyncio.run(_recognize(buf.getvalue()))
    if scale != 1.0:
        for line in lines:
            for word in line["words"]:
                for key in ("x", "y", "w", "h"):
                    word[key] = word[key] / scale
    return lines


def recognize_text(image) -> str:
    return "\n".join(line["text"] for line in recognize(image))
