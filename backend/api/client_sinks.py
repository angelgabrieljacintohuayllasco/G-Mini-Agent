"""
G-Mini Agent — Clientes de eventos que no usan Socket.IO.

El agente emite todo con `sio.emit(evento, datos, to=sid)`. Los clientes de la
API v1 (REST con SSE y WebSocket plano: CLI, extensión, dispositivos) usan un
sid virtual; `websocket_handler` envuelve `sio.emit` y entrega aquí los eventos
de esos sids. Los observadores (p. ej. una carita OLED) reciben además el
estado y la emoción del agente aunque la conversación sea de otro cliente.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from loguru import logger

Sink = Callable[[str, Any], None]

SID_PREFIX = "apiv1:"
OBSERVED_EVENTS = ("agent:status", "agent:emotion")

_sinks: dict[str, Sink] = {}
_observers: dict[str, Sink] = {}
_state: dict[str, Any] = {"status": "idle", "emotion": "neutral", "updated_at": time.time()}


def register(sid: str, sink: Sink, *, observe: bool = False) -> None:
    _sinks[sid] = sink
    if observe:
        _observers[sid] = sink


def unregister(sid: str) -> None:
    _sinks.pop(sid, None)
    _observers.pop(sid, None)


def is_client(sid: Any) -> bool:
    return isinstance(sid, str) and sid in _sinks


def _safe_call(sink: Sink, event: str, data: Any) -> None:
    try:
        sink(event, data)
    except Exception as exc:  # un cliente roto no debe afectar al agente
        logger.debug(f"client_sinks: error entregando {event}: {exc}")


def deliver(target: Any, event: str, data: Any) -> bool:
    """Entrega un evento si `target` es un cliente v1. Devuelve True si lo consumió."""
    _track_state(event, data)
    if event in OBSERVED_EVENTS:
        for sid, sink in list(_observers.items()):
            if sid != target:
                _safe_call(sink, event, data)
    sink = _sinks.get(target) if isinstance(target, str) else None
    if sink is None:
        return False
    _safe_call(sink, event, data)
    return True


def _track_state(event: str, data: Any) -> None:
    if not isinstance(data, dict):
        return
    if event == "agent:status" and data.get("status"):
        _state["status"] = str(data["status"])
        _state["updated_at"] = time.time()
    elif event == "agent:emotion" and data.get("emotion"):
        _state["emotion"] = str(data["emotion"])
        _state["updated_at"] = time.time()


def agent_state() -> dict[str, Any]:
    return dict(_state)
