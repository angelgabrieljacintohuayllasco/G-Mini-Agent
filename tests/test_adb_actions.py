"""Acciones de Android por ADB con un controlador falso."""

from __future__ import annotations

import base64

from backend.core.planner import Action, ActionPlanner
from tests.test_screen_text import LINES


class FakeADB:
    def __init__(self, connected=True):
        self.connected = connected
        self.keys: list[int] = []
        self.shells: list[str] = []
        self.swipes: list[tuple] = []

    def is_connected(self):
        return self.connected

    async def list_devices(self):
        return ["2e37f64f"] if self.connected else []

    async def press_key(self, keycode):
        self.keys.append(keycode)
        return self.connected

    async def swipe(self, x1, y1, x2, y2, duration_ms=300):
        self.swipes.append((x1, y1, x2, y2, duration_ms))
        return True

    async def shell(self, command):
        self.shells.append(command)
        return "Events injected: 1"

    async def screenshot(self):
        return b"\x89PNG fake" if self.connected else None


class FakeVision:
    async def ocr_lines(self, png):
        return LINES

    async def extract_text(self, image_bytes=None, monitor=0):
        return "Guardar cambios"


def _planner(adb):
    planner = ActionPlanner.__new__(ActionPlanner)
    planner._adb = adb
    planner._vision = FakeVision()
    return planner


async def run(planner, kind, **params):
    return await planner._execute_single(Action(type=kind, params=params))


async def test_status_keys_and_long_press():
    adb = FakeADB()
    planner = _planner(adb)
    status = await run(planner, "adb_status")
    assert status["data"] == {"connected": True, "devices": ["2e37f64f"]}
    assert (await run(planner, "adb_back"))["success"]
    assert (await run(planner, "adb_key", key="enter"))["success"]
    assert (await run(planner, "adb_key", key="66"))["success"]
    assert not (await run(planner, "adb_key", key="volar"))["success"]
    assert adb.keys == [4, 66, 66]
    await run(planner, "adb_long_press", x=100, y=200, duration_ms=50)
    assert adb.swipes == [(100, 200, 100, 200, 300)]  # mínimo 300 ms


async def test_open_app_only_accepts_package_ids():
    adb = FakeADB()
    planner = _planner(adb)
    assert (await run(planner, "adb_open_app", package="com.whatsapp"))["success"]
    bad = await run(planner, "adb_open_app", package="com.x; rm -rf /")
    assert not bad["success"] and len(adb.shells) == 1


async def test_screenshot_read_and_locate():
    planner = _planner(FakeADB())
    shot = await run(planner, "adb_screenshot")
    assert base64.b64decode(shot["data"]["image_base64"]).startswith(b"\x89PNG")
    assert (await run(planner, "adb_screen_read_text"))["data"]["text"] == "Guardar cambios"
    located = await run(planner, "adb_screen_locate_text", text="cancelar")
    assert located["success"] and located["data"]["matches"][0] == {"text": "Cancelar", "x": 440, "y": 150}


async def test_disconnected_device_is_explained():
    planner = _planner(FakeADB(connected=False))
    assert "No hay un Android" in (await run(planner, "adb_status"))["message"]
    assert "No pude capturar" in (await run(planner, "adb_screenshot"))["message"]
