"""
G-Mini Agent — Extension Bridge.
WebSocket bridge que comunica el backend con la extensión Chrome "G-Mini Agent Bridge".
Usa un endpoint WebSocket nativo de FastAPI (/ws/extension) para recibir/enviar JSON.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from loguru import logger
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

# ── Estado global del bridge ────────────────────────────────
_bridge: ExtensionBridge | None = None


def get_bridge() -> ExtensionBridge:
    """Retorna la instancia global del bridge."""
    global _bridge
    if _bridge is None:
        _bridge = ExtensionBridge()
    return _bridge


class ExtensionBridge:
    """
    Puente de comunicación con la extensión Chrome.
    La extensión conecta por WebSocket a /ws/extension y envía/recibe JSON:
      Envío (backend → extensión): {"event": "ext:command", "data": {"id": "...", "command": "...", "params": {...}}}
      Recepción (extensión → backend): {"event": "ext:response", "data": {"id": "...", "success": ..., ...}}
    """

    def __init__(self):
        self._ws: WebSocket | None = None
        self._pending: dict[str, asyncio.Future] = {}  # id → Future
        self._connected = False
        self._ext_info: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    # ── Propiedades ──────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def extension_info(self) -> dict[str, Any]:
        return self._ext_info

    # ── WebSocket Handler (registrado en main.py) ────────────

    async def handle_websocket(self, ws: WebSocket) -> None:
        """Handler principal del WebSocket. Se registra como ruta en FastAPI."""
        await ws.accept()
        async with self._lock:
            # Si ya hay una conexión, cerrarla
            if self._ws and self._ws.client_state == WebSocketState.CONNECTED:
                try:
                    await self._ws.close()
                except Exception:
                    pass
            self._ws = ws
            self._connected = True

        logger.info("[ExtBridge] Extensión Chrome conectada")

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(f"[ExtBridge] JSON inválido: {raw[:200]}")
                    continue

                event = msg.get("event", "")
                data = msg.get("data", {})
                await self._handle_message(event, data)

        except WebSocketDisconnect:
            logger.info("[ExtBridge] Extensión Chrome desconectada")
        except Exception as e:
            logger.warning(f"[ExtBridge] Error en WebSocket: {e}")
        finally:
            async with self._lock:
                # Si ya entró otra conexión (otra pestaña de la extensión, una
                # reconexión), es la vigente: no se toca su estado.
                if self._ws is ws:
                    self._ws = None
                    self._connected = False
                    for fid, fut in self._pending.items():
                        if not fut.done():
                            fut.set_exception(ConnectionError("Extension disconnected"))
                    self._pending.clear()

    async def _handle_message(self, event: str, data: dict) -> None:
        """Procesa un mensaje entrante de la extensión."""
        if event == "ext:hello":
            self._ext_info = data
            logger.info(
                f"[ExtBridge] Handshake OK — "
                f"extensionId={data.get('extensionId', '?')}, "
                f"tab={data.get('tabUrl', '?')}"
            )

        elif event == "ext:response":
            cmd_id = data.get("id", "")
            fut = self._pending.pop(cmd_id, None)
            if fut and not fut.done():
                fut.set_result(data)
            else:
                logger.warning(f"[ExtBridge] Response sin pending: id={cmd_id}")

        elif event == "ext:pong":
            logger.debug("[ExtBridge] Pong recibido")

        else:
            logger.debug(f"[ExtBridge] Evento desconocido: {event}")

    # ── Enviar comando a la extensión ────────────────────────

    async def send_command(
        self, command: str, params: dict | None = None, timeout: float = 15.0
    ) -> dict[str, Any]:
        """
        Envía un comando a la extensión y espera la respuesta.
        Retorna el dict de respuesta de la extensión.
        Lanza TimeoutError si no hay respuesta.
        Lanza ConnectionError si la extensión no está conectada.
        """
        if not self.is_connected or not self._ws:
            raise ConnectionError("Extension not connected")

        cmd_id = uuid.uuid4().hex[:12]
        fut: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._pending[cmd_id] = fut

        payload = json.dumps({
            "event": "ext:command",
            "data": {
                "id": cmd_id,
                "command": command,
                "params": params or {},
            },
        })

        try:
            await self._ws.send_text(payload)
        except Exception as e:
            self._pending.pop(cmd_id, None)
            raise ConnectionError(f"Failed to send command: {e}")

        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            raise TimeoutError(f"Extension command '{command}' timed out after {timeout}s")

    # ── Convenience methods (misma API que BrowserController) ──

    async def navigate(self, url: str) -> dict:
        return await self.send_command("navigate", {"url": url})

    async def click(self, selector: str, force: bool = False) -> dict:
        return await self.send_command("click", {"selector": selector, "force": force})

    async def type(self, selector: str, text: str, clear: bool = True) -> dict:
        return await self.send_command("type", {"selector": selector, "text": text, "clear": clear})

    async def fill(self, selector: str, text: str) -> dict:
        return await self.send_command("fill", {"selector": selector, "text": text})

    async def press(self, key: str) -> dict:
        return await self.send_command("press", {"key": key})

    async def scroll(self, direction: str = "down", amount: int = 3) -> dict:
        return await self.send_command("scroll", {"direction": direction, "amount": amount})

    async def hover(self, selector: str) -> dict:
        return await self.send_command("hover", {"selector": selector})

    async def select_option(self, selector: str, value: str) -> dict:
        return await self.send_command("select", {"selector": selector, "value": value})

    async def wait_for_selector(self, selector: str, timeout_ms: int = 15000, state: str = "visible") -> dict:
        return await self.send_command("wait_for", {"selector": selector, "timeout_ms": timeout_ms, "state": state}, timeout=timeout_ms / 1000 + 2)

    async def wait_for_load(self, state: str = "domcontentloaded", timeout_ms: int = 30000) -> dict:
        return await self.send_command("wait_load", {"timeout_ms": timeout_ms}, timeout=timeout_ms / 1000 + 2)

    async def go_back(self) -> dict:
        return await self.send_command("go_back")

    async def go_forward(self) -> dict:
        return await self.send_command("go_forward")

    async def remove_overlays(self) -> dict:
        return await self.send_command("remove_overlays")

    async def extract(self, selector: str = "body") -> dict:
        return await self.send_command("extract", {"selector": selector})

    async def get_dom(self, selector: str = "body", max_depth: int = 6, max_length: int = 12000) -> dict:
        return await self.send_command("get_dom", {"selector": selector, "max_depth": max_depth, "max_length": max_length})

    async def snapshot(self) -> dict:
        return await self.send_command("snapshot")

    async def evaluate(self, script: str) -> dict:
        return await self.send_command("eval", {"script": script})

    async def screenshot(self) -> dict:
        return await self.send_command("screenshot", timeout=10.0)

    async def get_page_info(self) -> dict:
        return await self.send_command("page_info")

    async def list_tabs(self) -> dict:
        return await self.send_command("tabs_list")

    async def switch_tab(self, index: int) -> dict:
        return await self.send_command("tab_switch", {"index": index})

    async def new_tab(self, url: str | None = None) -> dict:
        return await self.send_command("tab_new", {"url": url or "about:blank"})

    async def close_tab(self, index: int | None = None) -> dict:
        params = {}
        if index is not None:
            params["index"] = index
        return await self.send_command("tab_close", params)

    async def list_downloads(self, limit: int = 20) -> dict:
        return await self.send_command("downloads_list", {"limit": limit})

    async def ping(self) -> bool:
        """Verifica si la extensión responde."""
        if not self.is_connected:
            return False
        try:
            await self.send_command("snapshot", timeout=5.0)
            return True
        except Exception:
            return False
