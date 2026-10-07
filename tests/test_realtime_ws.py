"""Voz en tiempo real: las cabeceras de autenticación llegan con cualquier versión de websockets."""

from __future__ import annotations

import websockets

from backend.voice import realtime


async def test_auth_header_reaches_the_server():
    seen: dict[str, str | None] = {}

    async def handler(ws):
        seen["auth"] = ws.request.headers.get("Authorization")
        await ws.send("listo")

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        ws = await realtime._ws_connect(f"ws://127.0.0.1:{port}", {"Authorization": "Bearer clave"})
        try:
            assert await ws.recv() == "listo"
        finally:
            await ws.close()
    assert seen["auth"] == "Bearer clave"
