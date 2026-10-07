"""
G-Mini Agent - Conector Slack para GatewayService.
Entrega notificaciones, enruta mensajes remotos y procesa comandos básicos.
Usa slack-sdk SocketMode para recibir eventos en tiempo real.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

from loguru import logger

from backend.config import ROOT_DIR, config

try:
    from slack_sdk.web.async_client import AsyncWebClient
    from slack_sdk.socket_mode.aiohttp import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse
except Exception:
    AsyncWebClient = None  # type: ignore
    SocketModeClient = None  # type: ignore
    SocketModeRequest = None  # type: ignore
    SocketModeResponse = None  # type: ignore

if TYPE_CHECKING:
    from backend.core.gateway_service import GatewayService


SLACK_CHANNEL = "slack"
DEFAULT_SLACK_BOT_VAULT = "slack_bot"
DEFAULT_SLACK_APP_VAULT = "slack_app"


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "si", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


COMMAND_HANDLERS = {
    "/estado": "status",
    "/start": "start",
    "/pause": "pause",
    "/stop": "stop",
    "/modo": "mode",
    "/aprobar": "approve",
    "/cancelar": "cancel",
}


class SlackGatewayConnector:
    def __init__(self, gateway: "GatewayService"):
        self._gateway = gateway
        self._web_client: Any | None = None
        self._socket_client: Any | None = None
        self._task: asyncio.Task | None = None
        self._bot_user_id: str = ""
        self._last_error: str = ""
        self._last_status: str = "stopped"
        self._stop_requested = False

    @property
    def _channel_config(self) -> dict[str, Any]:
        raw = config.get("gateway", "channels", SLACK_CHANNEL, default={}) or {}
        return raw if isinstance(raw, dict) else {}

    @property
    def bot_token(self) -> str:
        token = str(self._channel_config.get("bot_token") or "").strip()
        if token:
            return token
        try:
            from backend.security.vault import get_api_key
            return str(get_api_key(DEFAULT_SLACK_BOT_VAULT) or "").strip()
        except Exception:
            return ""

    @property
    def app_token(self) -> str:
        token = str(self._channel_config.get("app_token") or "").strip()
        if token:
            return token
        try:
            from backend.security.vault import get_api_key
            return str(get_api_key(DEFAULT_SLACK_APP_VAULT) or "").strip()
        except Exception:
            return ""

    @property
    def default_channel_id(self) -> str:
        return str(self._channel_config.get("default_channel_id") or "").strip()

    @property
    def allowed_channel_ids(self) -> list[str]:
        raw = self._channel_config.get("allowed_channel_ids", [])
        if isinstance(raw, list):
            return [str(i).strip() for i in raw if str(i).strip()]
        return []

    @property
    def alias(self) -> str:
        return str(self._channel_config.get("alias") or "gmini").strip().lower()

    async def start(self) -> None:
        if AsyncWebClient is None or SocketModeClient is None:
            logger.warning("slack-sdk no instalado — conector Slack deshabilitado")
            return

        if self._task and not self._task.done():
            return

        bot = self.bot_token
        app = self.app_token
        if not bot or not app:
            logger.warning("Faltan tokens de Slack (bot_token / app_token) — conector deshabilitado")
            return

        self._stop_requested = False
        self._web_client = AsyncWebClient(token=bot)
        self._socket_client = SocketModeClient(app_token=app, web_client=self._web_client)
        self._socket_client.socket_mode_request_listeners.append(self._handle_socket_event)

        self._task = asyncio.create_task(self._run_client())
        logger.info("Gateway Slack iniciado")

    async def stop(self) -> None:
        self._stop_requested = True
        if self._socket_client:
            try:
                await self._socket_client.close()
            except Exception:
                pass
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._last_status = "stopped"
        logger.info("Gateway Slack detenido")

    async def _run_client(self) -> None:
        try:
            # Obtener bot user ID
            auth = await self._web_client.auth_test()
            self._bot_user_id = str(auth.get("user_id") or "")
            self._last_status = "ready"
            await self._socket_client.connect()
            # Mantener vivo
            while not self._stop_requested:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = str(exc)
            self._last_status = "error"
            logger.error(f"Slack connector error: {exc}")

    async def _handle_socket_event(self, client: Any, req: Any) -> None:
        """Procesa eventos SocketMode de Slack."""
        if SocketModeResponse is not None:
            await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

        if req.type == "events_api":
            event = (req.payload or {}).get("event", {})
            event_type = event.get("type", "")

            if event_type == "message" and not event.get("bot_id"):
                await self._handle_message(event)
            elif event_type == "app_mention":
                await self._handle_message(event)

        elif req.type == "slash_commands":
            await self._handle_slash_command(req.payload or {})

    async def _handle_message(self, event: dict) -> None:
        text = str(event.get("text") or "").strip()
        channel_id = str(event.get("channel") or "").strip()
        user_id = str(event.get("user") or "").strip()

        if not text or not channel_id:
            return
        if user_id == self._bot_user_id:
            return
        if self.allowed_channel_ids and channel_id not in self.allowed_channel_ids:
            return

        # Limpiar mención del bot
        text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
        session_key = f"slack:{channel_id}"

        await self._gateway.register_remote_session(
            SLACK_CHANNEL,
            session_key=session_key,
            display_name=user_id,
            meta={"channel_id": channel_id, "user_id": user_id},
        )

        # Comandos
        if text.startswith("/"):
            await self._process_command(session_key, channel_id, text)
            return

        # Mensaje normal → agente
        if self._gateway.agent_core is None:
            await self._send_text(channel_id, "El backend del agente no está disponible todavía.")
            return

        await self._gateway.agent_core.process_gateway_message(
            SLACK_CHANNEL,
            session_key,
            text,
            attachments=[],
        )

    async def _handle_slash_command(self, payload: dict) -> None:
        command = str(payload.get("command") or "").strip().lower()
        text = str(payload.get("text") or "").strip()
        channel_id = str(payload.get("channel_id") or "").strip()
        session_key = f"slack:{channel_id}"

        full_command = f"{command} {text}".strip() if text else command
        await self._process_command(session_key, channel_id, full_command)

    async def _process_command(self, session_key: str, channel_id: str, text: str) -> None:
        parts = text.strip().split(None, 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        action = COMMAND_HANDLERS.get(cmd)
        if not action:
            await self._send_text(channel_id, f"Comando no reconocido: {cmd}")
            return

        if action == "status":
            status = self._gateway.agent_core.get_runtime_status() if self._gateway.agent_core else {}
            msg = f"Estado: {status.get('status', 'desconocido')} | Modo: {status.get('mode', 'N/A')}"
            await self._send_text(channel_id, msg)
        elif action == "start":
            if self._gateway.agent_core:
                await self._gateway.agent_core.resume()
            await self._send_text(channel_id, "Agente reanudado.")
        elif action == "pause":
            if self._gateway.agent_core:
                await self._gateway.agent_core.pause()
            await self._send_text(channel_id, "Agente pausado.")
        elif action == "stop":
            if self._gateway.agent_core:
                await self._gateway.agent_core.stop_generation()
            await self._send_text(channel_id, "Agente detenido.")
        elif action == "mode":
            if args and self._gateway.agent_core:
                self._gateway.agent_core.set_mode(args, session_key=session_key)
                await self._send_text(channel_id, f"Modo cambiado a: {args}")
            else:
                await self._send_text(channel_id, "Uso: /modo <nombre_modo>")
        elif action == "approve":
            await self._gateway.resolve_remote_approval(session_key, approved=True, token=args)
            await self._send_text(channel_id, "Aprobado.")
        elif action == "cancel":
            await self._gateway.resolve_remote_approval(session_key, approved=False, token=args)
            await self._send_text(channel_id, "Cancelado.")

    async def send_notification(self, item: dict[str, Any]) -> None:
        session_key = str(item.get("session_key") or "")
        channel_id = self.resolve_channel_id(session_key)
        if not channel_id or not self._web_client:
            raise RuntimeError("No se pudo resolver channel_id de Slack o cliente no disponible.")

        text = self._render_notification_text(item)
        if not text:
            raise RuntimeError("La notificación de Slack no tiene texto.")

        # Dividir si es largo
        chunks = self._split_text(text)
        for chunk in chunks:
            await self._send_text(channel_id, chunk)

    def resolve_channel_id(self, session_key: str) -> str:
        normalized = str(session_key or "").strip()
        if not normalized or normalized == "main":
            return self.default_channel_id
        if ":" in normalized:
            return normalized.rsplit(":", 1)[-1].strip()
        return normalized

    async def _send_text(self, channel_id: str, text: str) -> None:
        if not self._web_client:
            return
        try:
            await self._web_client.chat_postMessage(channel=channel_id, text=text)
        except Exception as exc:
            logger.error(f"Error enviando mensaje a Slack {channel_id}: {exc}")

    def _render_notification_text(self, item: dict[str, Any]) -> str:
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        level = str(item.get("level") or "info").strip()

        label = {"warning": "Aviso", "error": "Error", "success": "Listo"}.get(level, "")
        heading = f"{label}: {title}" if label and title else title
        parts = [f"*{heading}*"] if heading else []
        if body:
            parts.append(body)
        return "\n".join(parts)

    def _split_text(self, text: str) -> list[str]:
        if len(text) <= 3000:
            return [text]
        chunks = []
        remaining = text
        while remaining:
            if len(remaining) <= 3000:
                chunks.append(remaining)
                break
            cut = remaining[:3000].rfind("\n")
            if cut < 500:
                cut = 3000
            chunks.append(remaining[:cut])
            remaining = remaining[cut:].lstrip("\n")
        return chunks

    async def get_runtime_state(self) -> dict[str, Any]:
        return {
            "channel": SLACK_CHANNEL,
            "status": self._last_status,
            "bot_user_id": self._bot_user_id,
            "default_channel": self.default_channel_id,
            "last_error": self._last_error,
        }
