"""Acciones de pantalla con OCR: leer, ubicar texto y elegir monitor."""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from backend.vision import windows_ocr
from backend.vision.text_locate import find_text, normalize

LINES = [
    {"text": "Configuración de la cuenta", "words": [
        {"text": "Configuración", "x": 30, "y": 40, "w": 240, "h": 40},
        {"text": "de", "x": 290, "y": 40, "w": 40, "h": 40},
        {"text": "la", "x": 345, "y": 40, "w": 25, "h": 40},
        {"text": "cuenta", "x": 385, "y": 40, "w": 120, "h": 40},
    ]},
    {"text": "Guardar cambios Cancelar", "words": [
        {"text": "Guardar", "x": 30, "y": 130, "w": 140, "h": 40},
        {"text": "cambios", "x": 185, "y": 130, "w": 145, "h": 40},
        {"text": "Cancelar", "x": 365, "y": 130, "w": 150, "h": 40},
    ]},
]


def test_normalize_ignores_case_accents_and_edges():
    assert normalize("  Configuración:  ") == "configuracion"
    assert normalize("GUARDAR   Cambios") == "guardar cambios"


def test_find_text_joins_words_and_ranks_exact_first():
    matches = find_text(LINES, "guardar cambios")
    assert matches[0]["text"] == "Guardar cambios"
    assert (matches[0]["x"], matches[0]["y"], matches[0]["w"]) == (30, 130, 300)
    assert find_text(LINES, "configuracion de")[0]["text"] == "Configuración de"
    assert find_text(LINES, "cancel")[0]["text"] == "Cancelar"
    assert find_text(LINES, "cancel", exact=True) == []
    assert find_text(LINES, "no existe") == []


@pytest.mark.skipif(not windows_ocr.available(), reason="OCR de Windows no disponible")
def test_windows_ocr_reads_spanish_with_boxes():
    img = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("segoeui.ttf", 40)
    except OSError:
        pytest.skip("sin la fuente Segoe UI")
    draw.text((30, 30), "Configuración de la cuenta", fill="black", font=font)
    draw.text((30, 120), "Guardar cambios", fill="black", font=font)
    lines = windows_ocr.recognize(img)
    match = find_text(lines, "guardar cambios")[0]
    assert 20 <= match["x"] <= 45 and 115 <= match["y"] <= 145


async def test_locate_action_returns_coordinates_in_click_space(monkeypatch):
    from backend.core import planner as planner_module
    from backend.core.planner import Action, ActionPlanner

    class FakeVision:
        _ocr_type = "fake"

        async def _capture_screen_with_retry(self, monitor=0):
            buf = io.BytesIO()
            Image.new("RGB", (1920, 1080), "white").save(buf, format="PNG")
            return buf.getvalue()

        async def ocr_lines(self, png):
            return LINES

    monkeypatch.setattr(planner_module, "_list_monitors", lambda: [
        {"monitor": 0, "left": 0, "top": 0, "width": 1920, "height": 1080},
        {"monitor": 1, "left": 0, "top": 0, "width": 1920, "height": 1080},
    ])
    monkeypatch.setattr("backend.vision.engine._get_logical_screen_size", lambda: (1920, 1080))
    planner = ActionPlanner.__new__(ActionPlanner)
    planner._vision = FakeVision()
    planner._screen_dims = None

    result = await planner._execute_single(Action(type="screen_locate_text", params={"text": "Guardar cambios"}))
    first = result["data"]["matches"][0]
    assert result["success"] and (first["x"], first["y"]) == (180, 150)

    # Tras un screenshot reducido a 1280 px, las coordenadas vienen en ese espacio (el que usa click).
    planner._screen_dims = {"logical_w": 1920, "logical_h": 1080, "sent_w": 1280, "sent_h": 720}
    result = await planner._execute_single(Action(type="screen_locate_text", params={"text": "Guardar cambios"}))
    first = result["data"]["matches"][0]
    assert (first["x"], first["y"], first["screen_x"]) == (120, 100, 180)

    missing = await planner._execute_single(Action(type="screen_locate_text", params={"text": "Eliminar"}))
    assert not missing["success"] and "No encontré" in missing["message"]


async def test_monitor_actions(monkeypatch):
    from backend.core import planner as planner_module
    from backend.core.planner import Action, ActionPlanner

    saved = {}
    monkeypatch.setattr(planner_module, "_list_monitors", lambda: [
        {"monitor": 0, "left": 0, "top": 0, "width": 3840, "height": 1080},
        {"monitor": 1, "left": 0, "top": 0, "width": 1920, "height": 1080},
        {"monitor": 2, "left": 1920, "top": 0, "width": 1920, "height": 1080},
    ])
    monkeypatch.setattr(planner_module.config, "set", lambda *keys, value: saved.__setitem__(keys, value))
    planner = ActionPlanner.__new__(ActionPlanner)
    planner._screen_dims = {"sent_w": 1}

    listed = await planner._execute_single(Action(type="screen_list_monitors", params={}))
    assert listed["success"] and len(listed["data"]["monitors"]) == 3
    chosen = await planner._execute_single(Action(type="screen_set_monitor", params={"monitor": 2}))
    assert chosen["success"] and saved == {("vision", "target_monitor"): 2} and planner._screen_dims is None
    bad = await planner._execute_single(Action(type="screen_set_monitor", params={"monitor": 7}))
    assert not bad["success"]
