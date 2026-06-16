"""
G-Mini Agent — Automation Engine (PC).
Controla mouse, teclado y acciones del sistema operativo.
Incluye kill switch (Ctrl+Shift+Esc) para seguridad.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import subprocess
import time
import unicodedata
from collections import deque
from pathlib import Path
from typing import Any, Iterator

from loguru import logger

try:
    import pyautogui
    pyautogui.FAILSAFE = True  # Mover al corner = abort
    pyautogui.PAUSE = 0.05  # Delay entre acciones
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False
    logger.warning("pyautogui no disponible — automatización deshabilitada")

try:
    from pynput import keyboard as pynput_kb
    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False
    logger.warning("pynput no disponible — kill switch limitado")

from backend.config import config
from backend.automation.chrome_profiles import ChromeProfileExplorer


class AutomationEngine:
    """
    Motor de automatización del agente.
    - Click, doble click, click derecho
    - Movimiento de mouse
    - Escritura de texto
    - Hotkeys / combinaciones de teclas
    - Scroll
    - Kill switch global para detener todo
    """

    def __init__(self):
        self._enabled = False
        self._kill_switch_active = False
        self._keyboard_listener = None
        self._keyboard_listener_ready = False
        self._pressed_keys: set[str] = set()
        self._input_buffer_size = max(
            10,
            min(int(config.get("automation", "input_event_buffer_size", default=200) or 200), 5000),
        )
        self._input_event_buffer: deque[dict[str, Any]] = deque(maxlen=self._input_buffer_size)
        self._registered_hotkeys: dict[str, dict[str, Any]] = {}
        self._input_listener_enabled = bool(
            config.get("automation", "input_listener_enabled_on_startup", default=False)
        )
        self._hotkey_default_cooldown_ms = max(
            0,
            min(int(config.get("automation", "hotkey_default_cooldown_ms", default=500) or 500), 60_000),
        )
        self._kill_switch_hotkey = str(
            config.get("automation", "kill_switch_hotkey", default="ctrl+shift+escape")
            or "ctrl+shift+escape"
        ).strip()
        kill_switch_keys = self._normalize_hotkey_keys(self._kill_switch_hotkey)
        if not kill_switch_keys:
            kill_switch_keys = ["ctrl", "shift", "esc"]
        self._kill_switch_combo = frozenset(kill_switch_keys)
        self._action_log: list[dict[str, Any]] = []
        self._chrome = ChromeProfileExplorer()
        self._downloads_dir = Path.home() / "Downloads"
        self._max_search_results = 100
        self._max_search_file_bytes = 1_000_000

    async def initialize(self) -> None:
        """Inicializa el motor de automatización y el kill switch."""
        self._enabled = config.get("automation", "enabled", default=True)

        if HAS_PYNPUT:
            listener_status = self._setup_keyboard_listener()
            if not listener_status.get("success"):
                logger.warning(listener_status.get("message", "No se pudo inicializar listener global"))
            elif self._input_listener_enabled:
                start_status = self.start_input_listener()
                if not start_status.get("success"):
                    logger.warning(start_status.get("message", "No se pudo habilitar input listener global"))
        else:
            self._input_listener_enabled = False

        if not HAS_PYAUTOGUI:
            logger.error("pyautogui no disponible")

        logger.info(f"AutomationEngine inicializado (enabled={self._enabled})")

    @staticmethod
    def _normalize_hotkey_key_token(value: Any) -> str:
        raw = str(value or "").strip().lower()
        if not raw:
            return ""
        raw = raw.replace("key.", "").strip("\"'")
        normalized = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
        normalized = normalized.replace("-", "_").replace(" ", "_")
        alias_map = {
            "control": "ctrl",
            "ctrl_l": "ctrl",
            "ctrl_r": "ctrl",
            "shift_l": "shift",
            "shift_r": "shift",
            "alt_l": "alt",
            "alt_r": "alt",
            "alt_gr": "alt",
            "option": "alt",
            "cmd_l": "cmd",
            "cmd_r": "cmd",
            "super": "cmd",
            "win": "cmd",
            "windows": "cmd",
            "escape": "esc",
            "return": "enter",
            "spacebar": "space",
            "caps_lock": "capslock",
            "page_up": "pageup",
            "page_down": "pagedown",
            "num_lock": "numlock",
            "print_screen": "printscreen",
            "scroll_lock": "scrolllock",
            "media_volume_up": "volumeup",
            "media_volume_down": "volumedown",
            "media_volume_mute": "volumemute",
        }
        if normalized in alias_map:
            return alias_map[normalized]
        return normalized

    def _normalize_hotkey_keys(self, keys: Any) -> list[str]:
        tokens: list[str] = []
        if isinstance(keys, str):
            tokens = [part.strip() for part in re.split(r"\s*\+\s*|\s*,\s*", keys) if part.strip()]
        elif isinstance(keys, (list, tuple, set)):
            tokens = [str(part).strip() for part in keys if str(part).strip()]
        else:
            token = str(keys or "").strip()
            if token:
                tokens = [token]

        normalized_tokens: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            normalized = self._normalize_hotkey_key_token(token)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            normalized_tokens.append(normalized)
        return normalized_tokens

    def _normalize_listener_key(self, key: Any) -> str:
        if key is None:
            return ""
        try:
            char_value = getattr(key, "char", None)
        except Exception:
            char_value = None
        if isinstance(char_value, str) and char_value.strip():
            return self._normalize_hotkey_key_token(char_value)
        key_name = getattr(key, "name", None)
        if isinstance(key_name, str) and key_name.strip():
            return self._normalize_hotkey_key_token(key_name)
        return self._normalize_hotkey_key_token(key)

    def _is_keyboard_listener_alive(self) -> bool:
        listener = self._keyboard_listener
        if listener is None:
            return False
        try:
            return bool(listener.is_alive())
        except Exception:
            return False

    def _serialize_hotkey(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "hotkey_id": str(entry.get("hotkey_id", "") or "").strip(),
            "keys": list(entry.get("keys") or []),
            "description": str(entry.get("description", "") or "").strip(),
            "cooldown_ms": int(entry.get("cooldown_ms", self._hotkey_default_cooldown_ms) or self._hotkey_default_cooldown_ms),
            "active": bool(entry.get("active")),
            "last_triggered_at": float(entry.get("last_triggered_at", 0.0) or 0.0),
            "trigger_count": int(entry.get("trigger_count", 0) or 0),
        }

    def _append_input_event(
        self,
        *,
        normalized_key: str,
        event_kind: str,
        matched_hotkeys: list[str] | None = None,
    ) -> None:
        if not self._input_listener_enabled:
            return
        self._input_event_buffer.append(
            {
                "type": "keyboard_input",
                "timestamp": time.time(),
                "key": normalized_key,
                "normalized_key": normalized_key,
                "event_kind": str(event_kind or "").strip() or "press",
                "matched_hotkeys": list(matched_hotkeys or []),
                "pressed_keys": sorted(self._pressed_keys),
            }
        )

    def _refresh_hotkey_active_state(self) -> None:
        for entry in self._registered_hotkeys.values():
            key_set = entry.get("key_set") or frozenset()
            if not key_set or not key_set.issubset(self._pressed_keys):
                entry["active"] = False

    def _match_registered_hotkeys(self) -> list[str]:
        matched_hotkeys: list[str] = []
        now_monotonic = time.monotonic()
        now_wall = time.time()
        for entry in self._registered_hotkeys.values():
            key_set = entry.get("key_set") or frozenset()
            if not key_set or not key_set.issubset(self._pressed_keys):
                if entry.get("active"):
                    entry["active"] = False
                continue
            cooldown_seconds = max(0.0, float(entry.get("cooldown_ms", self._hotkey_default_cooldown_ms) or 0.0) / 1000.0)
            last_triggered_monotonic = float(entry.get("last_triggered_monotonic", 0.0) or 0.0)
            if entry.get("active"):
                continue
            if cooldown_seconds > 0 and last_triggered_monotonic > 0 and (now_monotonic - last_triggered_monotonic) < cooldown_seconds:
                continue
            entry["active"] = True
            entry["last_triggered_monotonic"] = now_monotonic
            entry["last_triggered_at"] = now_wall
            entry["trigger_count"] = int(entry.get("trigger_count", 0) or 0) + 1
            matched_hotkeys.append(str(entry.get("hotkey_id", "") or "").strip())
        return matched_hotkeys

    def _trigger_kill_switch_if_needed(self) -> None:
        if self._kill_switch_active:
            return
        if self._kill_switch_combo and self._kill_switch_combo.issubset(self._pressed_keys):
            logger.warning("⚠️ KILL SWITCH ACTIVADO — Deteniendo automatización")
            self._kill_switch_active = True
            self._enabled = False
            self._log_action("kill_switch", combo=list(self._kill_switch_combo))

    def _handle_keyboard_press(self, key: Any) -> None:
        normalized_key = self._normalize_listener_key(key)
        if not normalized_key:
            return
        self._pressed_keys.add(normalized_key)
        self._trigger_kill_switch_if_needed()
        matched_hotkeys: list[str] = []
        if self._input_listener_enabled:
            matched_hotkeys = self._match_registered_hotkeys()
            self._append_input_event(
                normalized_key=normalized_key,
                event_kind="press",
                matched_hotkeys=matched_hotkeys,
            )

    def _handle_keyboard_release(self, key: Any) -> None:
        normalized_key = self._normalize_listener_key(key)
        if not normalized_key:
            return
        self._pressed_keys.discard(normalized_key)
        self._refresh_hotkey_active_state()
        if self._input_listener_enabled:
            self._append_input_event(
                normalized_key=normalized_key,
                event_kind="release",
                matched_hotkeys=[],
            )

    def _setup_keyboard_listener(self) -> dict[str, Any]:
        if not HAS_PYNPUT:
            self._keyboard_listener_ready = False
            return {
                "success": False,
                "available": False,
                "ready": False,
                "running": False,
                "message": "pynput no disponible: listener global deshabilitado",
            }

        if self._is_keyboard_listener_alive():
            self._keyboard_listener_ready = True
            return {
                "success": True,
                "available": True,
                "ready": True,
                "running": bool(self._input_listener_enabled),
                "message": "Listener de teclado global ya estaba listo",
            }

        def on_press(key: Any) -> None:
            try:
                self._handle_keyboard_press(key)
            except Exception as exc:
                logger.error(f"Error en on_press global: {exc}")

        def on_release(key: Any) -> None:
            try:
                self._handle_keyboard_release(key)
            except Exception as exc:
                logger.error(f"Error en on_release global: {exc}")

        try:
            self._keyboard_listener = pynput_kb.Listener(on_press=on_press, on_release=on_release)
            self._keyboard_listener.daemon = True
            self._keyboard_listener.start()
            self._keyboard_listener_ready = self._is_keyboard_listener_alive()
            if self._keyboard_listener_ready:
                logger.info(
                    f"Listener global de teclado listo (kill switch: {'+'.join(sorted(self._kill_switch_combo))})"
                )
                return {
                    "success": True,
                    "available": True,
                    "ready": True,
                    "running": bool(self._input_listener_enabled),
                    "message": "Listener de teclado global iniciado",
                }
        except Exception as exc:
            logger.error(f"No se pudo iniciar listener global de teclado: {exc}")

        self._keyboard_listener = None
        self._keyboard_listener_ready = False
        return {
            "success": False,
            "available": True,
            "ready": False,
            "running": False,
            "message": "No se pudo iniciar listener de teclado global",
        }

    def start_input_listener(self) -> dict[str, Any]:
        setup_status = self._setup_keyboard_listener()
        if not setup_status.get("success"):
            return setup_status
        if self._input_listener_enabled:
            return {
                **self.get_input_listener_status(),
                "message": "Listener global de teclado ya estaba activo",
            }
        self._input_listener_enabled = True
        self._log_action("input_listener_start", buffer_size=self._input_buffer_size)
        return {
            **self.get_input_listener_status(),
            "message": "Listener global de teclado activado",
        }

    def stop_input_listener(self) -> dict[str, Any]:
        if not HAS_PYNPUT:
            return {
                "success": False,
                "available": False,
                "ready": False,
                "running": False,
                "message": "pynput no disponible: listener global deshabilitado",
            }
        if not self._input_listener_enabled:
            return {
                **self.get_input_listener_status(),
                "message": "Listener global de teclado ya estaba inactivo",
            }
        self._input_listener_enabled = False
        self._refresh_hotkey_active_state()
        self._log_action("input_listener_stop")
        return {
            **self.get_input_listener_status(),
            "message": "Listener global de teclado desactivado",
        }

    def get_input_listener_status(self) -> dict[str, Any]:
        ready = self._is_keyboard_listener_alive() or self._keyboard_listener_ready
        available = bool(HAS_PYNPUT)
        success = available and ready
        running = bool(self._input_listener_enabled and ready)
        message = "Listener global listo" if success else "pynput no disponible o listener no inicializado"
        return {
            "success": success,
            "available": available,
            "ready": bool(ready),
            "running": running,
            "message": message,
            "kill_switch_active": bool(self._kill_switch_active),
            "kill_switch_hotkey": "+".join(self._normalize_hotkey_keys(self._kill_switch_hotkey)),
            "registered_hotkeys": len(self._registered_hotkeys),
            "buffered_events": len(self._input_event_buffer),
            "buffer_size": self._input_buffer_size,
        }

    def read_input_events(self, limit: int = 20, clear: bool = False) -> dict[str, Any]:
        status = self.get_input_listener_status()
        if not status.get("available"):
            return {
                **status,
                "events": [],
                "count": 0,
                "cleared": False,
                "message": "pynput no disponible: no se pueden leer eventos globales",
            }
        safe_limit = max(1, min(int(limit or 20), self._input_buffer_size))
        events = list(self._input_event_buffer)[-safe_limit:]
        if clear:
            self._input_event_buffer.clear()
        return {
            **status,
            "success": True,
            "events": events,
            "count": len(events),
            "cleared": bool(clear),
            "message": f"Eventos globales leidos: {len(events)}",
        }

    def register_hotkey(
        self,
        hotkey_id: str,
        keys: Any,
        description: str = "",
        cooldown_ms: int = 500,
    ) -> dict[str, Any]:
        status = self.get_input_listener_status()
        if not status.get("available"):
            return {
                **status,
                "message": "pynput no disponible: no se pueden registrar hotkeys globales",
            }
        normalized_hotkey_id = str(hotkey_id or "").strip()
        if not normalized_hotkey_id:
            return {
                **status,
                "success": False,
                "message": "hotkey_id vacio",
            }
        normalized_keys = self._normalize_hotkey_keys(keys)
        if not normalized_keys:
            return {
                **status,
                "success": False,
                "message": "keys vacias o invalidas",
            }
        key_set = frozenset(normalized_keys)
        if key_set == self._kill_switch_combo or normalized_hotkey_id.lower() in {"kill_switch", "__kill_switch__"}:
            return {
                **status,
                "success": False,
                "message": "La combinacion del kill switch esta reservada y no puede registrarse",
            }
        safe_cooldown_ms = max(0, min(int(cooldown_ms or self._hotkey_default_cooldown_ms), 60_000))
        replaced = normalized_hotkey_id in self._registered_hotkeys
        entry = {
            "hotkey_id": normalized_hotkey_id,
            "keys": normalized_keys,
            "key_set": key_set,
            "description": str(description or "").strip(),
            "cooldown_ms": safe_cooldown_ms,
            "active": False,
            "last_triggered_at": 0.0,
            "last_triggered_monotonic": 0.0,
            "trigger_count": 0,
        }
        self._registered_hotkeys[normalized_hotkey_id] = entry
        self._log_action(
            "input_hotkey_register",
            hotkey_id=normalized_hotkey_id,
            keys=normalized_keys,
            replaced=replaced,
            cooldown_ms=safe_cooldown_ms,
        )
        message = "Hotkey global reemplazada" if replaced else "Hotkey global registrada"
        if not self._input_listener_enabled:
            message += " (listener inactivo)"
        return {
            **self.get_input_listener_status(),
            "success": True,
            "replaced": replaced,
            "hotkey": self._serialize_hotkey(entry),
            "message": message,
        }

    def unregister_hotkey(self, hotkey_id: str) -> dict[str, Any]:
        status = self.get_input_listener_status()
        if not status.get("available"):
            return {
                **status,
                "message": "pynput no disponible: no se pueden eliminar hotkeys globales",
            }
        normalized_hotkey_id = str(hotkey_id or "").strip()
        if not normalized_hotkey_id:
            return {
                **status,
                "success": False,
                "message": "hotkey_id vacio",
            }
        removed = self._registered_hotkeys.pop(normalized_hotkey_id, None)
        if removed is None:
            return {
                **status,
                "success": False,
                "message": f"No existe hotkey global registrada: {normalized_hotkey_id}",
            }
        self._log_action("input_hotkey_unregister", hotkey_id=normalized_hotkey_id)
        return {
            **self.get_input_listener_status(),
            "success": True,
            "removed": self._serialize_hotkey(removed),
            "message": f"Hotkey global eliminada: {normalized_hotkey_id}",
        }

    def list_hotkeys(self) -> list[dict[str, Any]]:
        return [
            self._serialize_hotkey(entry)
            for _, entry in sorted(self._registered_hotkeys.items(), key=lambda item: item[0].lower())
        ]

    def _check_enabled(self) -> bool:
        """Verifica si la automatización está habilitada."""
        if self._kill_switch_active:
            logger.warning("Automatización bloqueada por kill switch")
            return False
        if not self._enabled:
            logger.warning("Automatización deshabilitada")
            return False
        if not HAS_PYAUTOGUI:
            return False
        return True

    def _log_action(self, action: str, **kwargs) -> None:
        """Registra una acción para auditoría."""
        entry = {
            "action": action,
            "timestamp": time.time(),
            **kwargs,
        }
        self._action_log.append(entry)
        # Mantener solo las últimas 100 acciones
        if len(self._action_log) > 100:
            self._action_log = self._action_log[-100:]

    # ── Mouse Actions ─────────────────────────────────────

    async def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> bool:
        """
        Hace clic en una posición.
        button: "left", "right", "middle"
        """
        if not self._check_enabled():
            return False

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: pyautogui.click(x, y, clicks=clicks, button=button)
            )
            self._log_action("click", x=x, y=y, button=button, clicks=clicks)
            logger.debug(f"Click ({button}): ({x}, {y}) x{clicks}")
            return True
        except Exception as e:
            logger.error(f"Error en click: {e}")
            return False

    async def double_click(self, x: int, y: int) -> bool:
        """Doble clic."""
        return await self.click(x, y, clicks=2)

    async def right_click(self, x: int, y: int) -> bool:
        """Clic derecho."""
        return await self.click(x, y, button="right")

    async def move_to(self, x: int, y: int, duration: float = 0.3) -> bool:
        """Mueve el mouse a una posición."""
        if not self._check_enabled():
            return False

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: pyautogui.moveTo(x, y, duration=duration)
            )
            self._log_action("move", x=x, y=y)
            return True
        except Exception as e:
            logger.error(f"Error moviendo mouse: {e}")
            return False

    async def get_mouse_position_async(self) -> dict[str, int] | None:
        """Obtiene la posición actual del cursor."""
        if not self._check_enabled():
            return None

        try:
            loop = asyncio.get_event_loop()
            position = await loop.run_in_executor(None, pyautogui.position)
            return {"x": int(position.x), "y": int(position.y)}
        except Exception as e:
            logger.error(f"Error obteniendo posición del mouse: {e}")
            return None

    @staticmethod
    def _normalize_mouse_button(button: str) -> str:
        normalized = str(button or "left").strip().lower()
        if normalized not in {"left", "right", "middle"}:
            return "left"
        return normalized

    async def drag_between(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: float = 0.5,
        button: str = "left",
    ) -> bool:
        """Arrastra desde un punto origen hasta un punto destino."""
        if not self._check_enabled():
            return False

        try:
            normalized_button = self._normalize_mouse_button(button)
            normalized_duration = max(0.0, min(float(duration or 0.0), 5.0))

            def _perform_drag() -> None:
                pyautogui.moveTo(int(start_x), int(start_y), duration=0.0)
                pyautogui.mouseDown(button=normalized_button)
                try:
                    pyautogui.moveTo(int(end_x), int(end_y), duration=normalized_duration)
                finally:
                    pyautogui.mouseUp(button=normalized_button)

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _perform_drag)
            self._log_action(
                "drag",
                start_x=int(start_x),
                start_y=int(start_y),
                end_x=int(end_x),
                end_y=int(end_y),
                duration=normalized_duration,
                button=normalized_button,
            )
            logger.debug(
                f"Drag ({normalized_button}): ({int(start_x)}, {int(start_y)}) -> "
                f"({int(end_x)}, {int(end_y)}) dur={normalized_duration:.2f}s"
            )
            return True
        except Exception as e:
            logger.error(f"Error en drag: {e}")
            return False

    async def drag_to(self, x: int, y: int, duration: float = 0.5, button: str = "left") -> bool:
        """Arrastra desde la posición actual a (x, y)."""
        current_position = await self.get_mouse_position_async()
        if not current_position:
            logger.error("No se pudo obtener la posición actual del mouse para drag_to")
            return False
        return await self.drag_between(
            start_x=int(current_position["x"]),
            start_y=int(current_position["y"]),
            end_x=int(x),
            end_y=int(y),
            duration=duration,
            button=button,
        )

    # ── Keyboard Actions ──────────────────────────────────

    async def type_text(self, text: str, interval: float = 0.02) -> bool:
        """Escribe texto usando clipboard (soporta Unicode) o fallback a write."""
        if not self._check_enabled():
            return False

        try:
            loop = asyncio.get_event_loop()

            def _type_via_clipboard():
                import pyperclip
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.05)

            try:
                await loop.run_in_executor(None, _type_via_clipboard)
            except Exception:
                # Fallback: pyautogui.write soporta más que typewrite
                await loop.run_in_executor(
                    None, lambda: pyautogui.write(text, interval=interval)
                )
            self._log_action("type", text=text[:50])
            logger.debug(f"Texto escrito: {text[:50]}...")
            return True
        except Exception as e:
            logger.error(f"Error escribiendo texto: {e}")
            return False

    async def write_text(self, text: str) -> bool:
        """Escribe texto usando el portapapeles (soporta unicode)."""
        if not self._check_enabled():
            return False

        try:
            import pyperclip
            loop = asyncio.get_event_loop()

            async def _paste():
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")

            await loop.run_in_executor(None, lambda: (
                __import__('pyperclip').copy(text),
                pyautogui.hotkey("ctrl", "v"),
            ))
            self._log_action("write", text=text[:50])
            return True
        except Exception as e:
            # Fallback a typewrite
            logger.debug(f"pyperclip no disponible, usando typewrite: {e}")
            return await self.type_text(text)

    async def focus_and_write_text(
        self,
        x: int,
        y: int,
        text: str,
        clear: bool = True,
        submit: bool = False,
    ) -> bool:
        """
        Enfoca un campo con click y luego escribe/pega.
        Util para formularios web o inputs que requieren foco previo.
        """
        if not self._check_enabled():
            return False

        try:
            loop = asyncio.get_event_loop()

            def _focus_and_write() -> None:
                pyautogui.click(x, y)
                time.sleep(0.12)
                if clear:
                    pyautogui.hotkey("ctrl", "a")
                    time.sleep(0.05)
                    pyautogui.press("backspace")
                    time.sleep(0.05)
                try:
                    import pyperclip
                    pyperclip.copy(text)
                    pyautogui.hotkey("ctrl", "v")
                except Exception:
                    pyautogui.typewrite(text, interval=0.02)
                if submit:
                    time.sleep(0.05)
                    pyautogui.press("enter")

            await loop.run_in_executor(None, _focus_and_write)
            self._log_action(
                "focus_write",
                x=x,
                y=y,
                text=text[:50],
                clear=clear,
                submit=submit,
            )
            return True
        except Exception as e:
            logger.error(f"Error en focus_and_write_text: {e}")
            return False

    async def press_key(self, key: str) -> bool:
        """Presiona una tecla (enter, tab, escape, etc.)."""
        if not self._check_enabled():
            return False

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: pyautogui.press(key))
            self._log_action("key", key=key)
            return True
        except Exception as e:
            logger.error(f"Error presionando tecla: {e}")
            return False

    async def hotkey(self, *keys: str) -> bool:
        """Ejecuta una combinación de teclas (ej: hotkey('ctrl', 'c'))."""
        if not self._check_enabled():
            return False

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: pyautogui.hotkey(*keys))
            self._log_action("hotkey", keys=list(keys))
            logger.debug(f"Hotkey: {'+'.join(keys)}")
            return True
        except Exception as e:
            logger.error(f"Error en hotkey: {e}")
            return False

    # ── Scroll ────────────────────────────────────────────

    async def scroll(self, clicks: int, x: int | None = None, y: int | None = None) -> bool:
        """Scroll. clicks > 0 = arriba, clicks < 0 = abajo."""
        if not self._check_enabled():
            return False

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: pyautogui.scroll(clicks, x=x, y=y)
            )
            self._log_action("scroll", clicks=clicks, x=x, y=y)
            return True
        except Exception as e:
            logger.error(f"Error en scroll: {e}")
            return False

    # ── Screen Info ───────────────────────────────────────

    def get_screen_size(self) -> tuple[int, int]:
        """Retorna el tamaño de la pantalla."""
        if HAS_PYAUTOGUI:
            return pyautogui.size()
        return (1920, 1080)

    def get_mouse_position(self) -> tuple[int, int]:
        """Retorna la posición actual del mouse."""
        if HAS_PYAUTOGUI:
            return pyautogui.position()
        return (0, 0)

    # ── Control ───────────────────────────────────────────

    def enable(self) -> None:
        """Habilita la automatización."""
        self._enabled = True
        self._kill_switch_active = False
        logger.info("Automatización habilitada")

    def disable(self) -> None:
        """Deshabilita la automatización."""
        self._enabled = False
        logger.info("Automatización deshabilitada")

    def get_action_log(self, limit: int = 20) -> list[dict]:
        """Retorna el log de acciones recientes."""
        return self._action_log[-limit:]

    def discover_chrome_profiles(self) -> list[dict[str, Any]]:
        """Retorna los perfiles reales de Chrome detectados en disco."""
        profiles = self._chrome.discover()
        self._log_action("chrome_discover_profiles", count=len(profiles))
        return profiles

    def open_chrome_profile(
        self,
        query: str | None = None,
        url: str | None = None,
        new_window: bool = True,
    ) -> dict[str, Any]:
        """Abre Chrome con un perfil humano existente."""
        process, profile = self._chrome.open(query=query, url=url, new_window=new_window)
        self._log_action(
            "chrome_open_profile",
            query=query or "",
            profile_dir=profile["dir_name"],
            url=url or "",
            pid=process.pid,
        )
        return {"pid": process.pid, "profile": profile}

    def open_chrome_automation_profile(
        self,
        profile_name: str = "chrome-agent-profile",
        url: str | None = None,
        new_window: bool = True,
    ) -> dict[str, Any]:
        """Abre Chrome con un user-data-dir dedicado al agente."""
        process, profile_dir = self._chrome.open_automation_profile(
            profile_name=profile_name,
            url=url,
            new_window=new_window,
        )
        self._log_action(
            "chrome_open_automation_profile",
            profile_name=profile_name,
            profile_dir=str(profile_dir),
            url=url or "",
            pid=process.pid,
        )
        return {"pid": process.pid, "profile_dir": str(profile_dir)}

    @staticmethod
    def _normalize_application_key(value: str) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        normalized = unicodedata.normalize("NFKD", text)
        normalized = normalized.encode("ascii", "ignore").decode("ascii")
        return " ".join(normalized.split())

    def _resolve_application_command(self, application: str) -> tuple[str, str]:
        raw_application = str(application or "").strip().strip("\"'")
        if not raw_application:
            raise ValueError("application vacia")

        normalized = self._normalize_application_key(raw_application)
        aliases = {
            "notepad": "notepad.exe",
            "notepad.exe": "notepad.exe",
            "bloc de notas": "notepad.exe",
            "editor de texto": "notepad.exe",
            "calculadora": "calc.exe",
            "calculator": "calc.exe",
            "calc": "calc.exe",
            "calc.exe": "calc.exe",
            "paint": "mspaint.exe",
            "mspaint": "mspaint.exe",
            "mspaint.exe": "mspaint.exe",
            "explorer": "explorer.exe",
            "explorer.exe": "explorer.exe",
            "explorador": "explorer.exe",
            "explorador de archivos": "explorer.exe",
            "cmd": "cmd.exe",
            "cmd.exe": "cmd.exe",
            "command prompt": "cmd.exe",
            "simbolo del sistema": "cmd.exe",
            "powershell": "powershell.exe",
            "powershell.exe": "powershell.exe",
            "windows powershell": "powershell.exe",
            # Navegadores
            "chrome": "chrome.exe",
            "google chrome": "chrome.exe",
            "chrome.exe": "chrome.exe",
            "firefox": "firefox.exe",
            "mozilla firefox": "firefox.exe",
            "firefox.exe": "firefox.exe",
            "edge": "msedge.exe",
            "microsoft edge": "msedge.exe",
            "msedge": "msedge.exe",
            "msedge.exe": "msedge.exe",
            "brave": "brave.exe",
            "brave.exe": "brave.exe",
            # Otras apps comunes
            "spotify": "spotify.exe",
            "spotify.exe": "spotify.exe",
            "discord": "discord.exe",
            "discord.exe": "discord.exe",
            "code": "code.exe",
            "visual studio code": "code.exe",
            "vscode": "code.exe",
            "word": "winword.exe",
            "microsoft word": "winword.exe",
            "excel": "excel.exe",
            "microsoft excel": "excel.exe",
            "teams": "ms-teams.exe",
            "microsoft teams": "ms-teams.exe",
        }
        resolved = aliases.get(normalized, None)
        if resolved is None:
            logger.warning(f"Aplicación no reconocida: {raw_application!r} (no está en aliases conocidos)")
            resolved = raw_application

        # En Windows, resolver rutas absolutas para apps que no están en PATH
        if os.name == "nt" and not Path(resolved).is_absolute():
            import shutil
            if not shutil.which(resolved):
                win_app_paths: dict[str, list[str]] = {
                    "chrome.exe": [
                        str(Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
                        str(Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
                        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
                    ],
                    "brave.exe": [
                        str(Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"),
                        str(Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"),
                        str(Path(os.environ.get("LOCALAPPDATA", "")) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"),
                    ],
                    "discord.exe": [
                        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Discord" / "Update.exe"),
                    ],
                    "spotify.exe": [
                        str(Path(os.environ.get("APPDATA", "")) / "Spotify" / "Spotify.exe"),
                    ],
                }
                for candidate in win_app_paths.get(resolved, []):
                    if Path(candidate).is_file():
                        logger.info(f"Aplicación '{resolved}' encontrada en: {candidate}")
                        resolved = candidate
                        break

        return raw_application, resolved

    def open_application(
        self,
        application: str,
        args: list[str] | str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Abre una aplicacion local del sistema operativo de forma nativa."""
        raw_application, resolved_application = self._resolve_application_command(application)

        if cwd:
            cwd_path = self.resolve_local_path(cwd)
            if not cwd_path.exists() or not cwd_path.is_dir():
                raise ValueError(f"cwd invalido: {cwd}")
            resolved_cwd = str(cwd_path)
        else:
            resolved_cwd = None

        normalized_args: list[str]
        if isinstance(args, str):
            text = args.strip()
            normalized_args = shlex.split(text, posix=False) if text else []
        elif isinstance(args, list):
            normalized_args = [str(item) for item in args if str(item).strip()]
        else:
            normalized_args = []

        command = [resolved_application, *normalized_args]
        creationflags = 0
        if os.name == "nt":
            creationflags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            creationflags |= int(getattr(subprocess, "DETACHED_PROCESS", 0))

        try:
            process = subprocess.Popen(
                command,
                cwd=resolved_cwd,
                close_fds=True,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"No se encontro la aplicacion '{raw_application}' ({resolved_application})"
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"No se pudo abrir la aplicacion '{raw_application}': {exc}"
            ) from exc

        self._log_action(
            "open_application",
            application=raw_application,
            resolved_application=resolved_application,
            args=normalized_args,
            cwd=resolved_cwd or "",
            pid=process.pid,
        )
        logger.info(
            "Aplicacion abierta: {} -> {} (pid={})",
            raw_application,
            resolved_application,
            process.pid,
        )
        return {
            "pid": process.pid,
            "application": raw_application,
            "resolved_application": resolved_application,
            "args": normalized_args,
            "cwd": resolved_cwd or "",
            "command": command,
        }

    def list_recent_downloads(
        self,
        limit: int = 10,
        recency_seconds: int = 900,
        expected_ext: str | None = None,
        filename_contains: str | None = None,
    ) -> list[dict[str, Any]]:
        """Inspecciona archivos recientes en Downloads para confirmar descargas reales."""
        now = time.time()
        expected_ext = (expected_ext or "").strip().lower()
        filename_contains = (filename_contains or "").strip().lower()
        candidate_dirs = [
            self._downloads_dir,
            self._downloads_dir / "G-Mini-Agent",
        ]
        items: list[dict[str, Any]] = []

        for base_dir in candidate_dirs:
            if not base_dir.exists():
                continue
            for entry in base_dir.iterdir():
                if not entry.is_file():
                    continue
                stat = entry.stat()
                age_seconds = now - stat.st_mtime
                if age_seconds > recency_seconds:
                    continue
                suffix = entry.suffix.lower()
                if expected_ext and suffix != expected_ext:
                    continue
                if filename_contains and filename_contains not in entry.name.lower():
                    continue
                items.append(
                    {
                        "path": str(entry),
                        "filename": entry.name,
                        "suffix": suffix,
                        "size_bytes": stat.st_size,
                        "modified_ts": stat.st_mtime,
                        "age_seconds": round(age_seconds, 1),
                    }
                )

        items.sort(key=lambda item: item["modified_ts"], reverse=True)
        self._log_action(
            "downloads_check",
            limit=limit,
            recency_seconds=recency_seconds,
            expected_ext=expected_ext,
            filename_contains=filename_contains,
            found=len(items),
        )
        return items[:limit]

    def resolve_local_path(self, raw_path: str) -> Path:
        """Normaliza rutas del usuario para operaciones locales de archivos."""
        text = str(raw_path or "").strip().strip("\"'")
        if not text:
            raise ValueError("path vacio")
        if text.startswith("$HOME"):
            text = text.replace("$HOME", str(Path.home()), 1)
        elif text.startswith("~"):
            text = str(Path.home()) + text[1:]
        text = os.path.expandvars(text)
        return Path(text).expanduser()

    def write_text_file(
        self,
        path: str,
        text: str,
        *,
        append: bool = False,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        """Escribe texto directamente en disco de forma confiable."""
        resolved = self.resolve_local_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if append:
            with resolved.open("a", encoding=encoding) as handle:
                handle.write(text)
        else:
            resolved.write_text(text, encoding=encoding)
        stat = resolved.stat()
        self._log_action(
            "file_write_text",
            path=str(resolved),
            append=append,
            size_bytes=stat.st_size,
        )
        return {
            "path": str(resolved),
            "exists": True,
            "size_bytes": stat.st_size,
            "encoding": encoding,
            "append": append,
        }

    def file_exists(self, path: str) -> dict[str, Any]:
        """Verifica existencia de un archivo local."""
        resolved = self.resolve_local_path(path)
        exists = resolved.exists()
        data: dict[str, Any] = {
            "path": str(resolved),
            "exists": exists,
        }
        if exists:
            stat = resolved.stat()
            data["size_bytes"] = stat.st_size
            data["modified_ts"] = stat.st_mtime
        self._log_action("file_exists", path=str(resolved), exists=exists)
        return data

    def list_files(
        self,
        path: str | None = None,
        *,
        pattern: str = "*",
        recursive: bool = False,
        include_hidden: bool = False,
        include_dirs: bool = False,
        max_results: int = 200,
    ) -> dict[str, Any]:
        """Lista archivos locales fuera del workspace con contrato compatible."""
        base_path = self.resolve_local_path(path) if str(path or "").strip() else Path.cwd().resolve()
        if not base_path.exists():
            raise FileNotFoundError(f"ruta no encontrada: {base_path}")

        if base_path.is_file():
            entries = [self._build_local_file_entry(base_path, base_path.parent)]
            data = {
                "base_path": str(base_path),
                "entries": entries,
                "count": len(entries),
                "truncated": False,
            }
            self._log_action("file_list", base_path=str(base_path), count=len(entries), truncated=False)
            return data

        iterator = base_path.rglob(pattern) if recursive else base_path.glob(pattern)
        entries: list[dict[str, Any]] = []
        truncated = False
        limit = max(1, int(max_results))

        for candidate in iterator:
            if not include_hidden and self._is_hidden_local(candidate, base_path):
                continue
            if candidate.is_dir() and not include_dirs:
                continue
            entries.append(self._build_local_file_entry(candidate, base_path))
            if len(entries) >= limit:
                truncated = True
                break

        data = {
            "base_path": str(base_path),
            "entries": entries,
            "count": len(entries),
            "truncated": truncated,
        }
        self._log_action(
            "file_list",
            base_path=str(base_path),
            pattern=pattern,
            recursive=recursive,
            include_hidden=include_hidden,
            include_dirs=include_dirs,
            count=len(entries),
            truncated=truncated,
        )
        return data

    def read_text_file(
        self,
        path: str,
        *,
        start_line: int = 1,
        max_lines: int = 200,
        max_chars: int | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        """Lee un archivo de texto local fuera del workspace con truncado seguro."""
        resolved = self.resolve_local_path(path)
        if not resolved.exists():
            raise FileNotFoundError(f"archivo no encontrado: {resolved}")
        if not resolved.is_file():
            raise IsADirectoryError(f"la ruta es un directorio: {resolved}")

        content = resolved.read_text(encoding=encoding, errors="replace")
        lines = content.splitlines()
        total_lines = len(lines)

        safe_start = max(1, int(start_line))
        safe_max_lines = max(1, int(max_lines))
        start_index = safe_start - 1
        end_index = min(total_lines, start_index + safe_max_lines)

        excerpt = "\n".join(lines[start_index:end_index])
        char_limit = max_chars if max_chars is not None else 20000
        truncated = False
        if len(excerpt) > char_limit:
            excerpt = excerpt[:char_limit]
            truncated = True
        if end_index < total_lines:
            truncated = True

        self._log_action(
            "file_read_text",
            path=str(resolved),
            start_line=safe_start,
            max_lines=safe_max_lines,
            truncated=truncated,
        )
        return {
            "path": str(resolved),
            "content": excerpt,
            "start_line": safe_start,
            "end_line": end_index,
            "total_lines": total_lines,
            "truncated": truncated,
            "encoding": encoding,
        }

    def read_text_files(
        self,
        paths: list[str],
        *,
        start_line: int = 1,
        max_lines: int = 200,
        max_chars_per_file: int | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        """Lee múltiples archivos de texto locales con contrato compatible al workspace."""
        items: list[dict[str, Any]] = []
        for raw_path in paths:
            item = self.read_text_file(
                str(raw_path),
                start_line=start_line,
                max_lines=max_lines,
                max_chars=max_chars_per_file,
                encoding=encoding,
            )
            items.append(item)
        data = {
            "count": len(items),
            "files": items,
        }
        self._log_action(
            "file_read_batch",
            count=len(items),
            start_line=start_line,
            max_lines=max_lines,
            max_chars_per_file=max_chars_per_file,
        )
        return data

    def read_text_file_tail(
        self,
        path: str,
        *,
        max_chars: int = 20000,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        """Lee el sufijo de un archivo de texto local sin cargarlo completo en memoria."""
        resolved = self.resolve_local_path(path)
        if not resolved.exists():
            raise FileNotFoundError(f"archivo no encontrado: {resolved}")
        if not resolved.is_file():
            raise IsADirectoryError(f"la ruta es un directorio: {resolved}")

        safe_max_chars = max(1, int(max_chars))
        with resolved.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            total_bytes = handle.tell()
            byte_window = max(safe_max_chars * 4, 4096)
            read_from = max(0, total_bytes - byte_window)
            handle.seek(read_from, os.SEEK_SET)
            raw = handle.read()

        excerpt = raw.decode(encoding, errors="replace")
        if len(excerpt) > safe_max_chars:
            excerpt = excerpt[-safe_max_chars:]
        truncated = total_bytes > len(raw)

        self._log_action(
            "file_read_text_tail",
            path=str(resolved),
            max_chars=safe_max_chars,
            truncated=truncated,
        )
        return {
            "path": str(resolved),
            "content": excerpt,
            "max_chars": safe_max_chars,
            "truncated": truncated,
            "encoding": encoding,
            "read_mode": "tail",
            "total_bytes": total_bytes,
        }

    def _build_local_file_entry(self, path: Path, base_path: Path) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "name": path.name,
            "path": str(path),
            "relative_path": self._relative_local_path(path, base_path),
            "is_dir": path.is_dir(),
        }
        if path.is_file():
            try:
                entry["size_bytes"] = path.stat().st_size
            except OSError:
                entry["size_bytes"] = None
        return entry

    def _relative_local_path(self, path: Path, base_path: Path) -> str:
        try:
            return str(path.resolve().relative_to(base_path.resolve()))
        except ValueError:
            return str(path.resolve())

    def _is_hidden_local(self, path: Path, base_path: Path) -> bool:
        try:
            relative = path.relative_to(base_path)
        except ValueError:
            relative = path
        return any(part.startswith(".") for part in relative.parts if part not in {".", ".."})

    def replace_text(
        self,
        path: str,
        *,
        find: str,
        replace: str,
        count: int = 1,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        """Reemplaza texto en un archivo local fuera del workspace."""
        resolved = self.resolve_local_path(path)
        if not resolved.exists():
            raise FileNotFoundError(f"archivo no encontrado: {resolved}")
        if not resolved.is_file():
            raise IsADirectoryError(f"la ruta es un directorio: {resolved}")
        if not find:
            raise ValueError("find vacio")

        content = resolved.read_text(encoding=encoding, errors="replace")
        occurrences = content.count(find)
        if occurrences == 0:
            data = {
                "path": str(resolved),
                "replaced_count": 0,
                "occurrences_found": 0,
                "changed": False,
                "encoding": encoding,
            }
            self._log_action(
                "file_replace_text",
                path=str(resolved),
                find=find,
                replace=replace,
                count=count,
                replaced_count=0,
                changed=False,
            )
            return data

        replace_count = int(count)
        if replace_count <= 0:
            updated = content.replace(find, replace)
            replaced_count = occurrences
        else:
            updated = content.replace(find, replace, replace_count)
            replaced_count = min(occurrences, replace_count)

        resolved.write_text(updated, encoding=encoding)
        data = {
            "path": str(resolved),
            "replaced_count": replaced_count,
            "occurrences_found": occurrences,
            "changed": replaced_count > 0,
            "encoding": encoding,
        }
        self._log_action(
            "file_replace_text",
            path=str(resolved),
            find=find,
            replace=replace,
            count=count,
            replaced_count=replaced_count,
            changed=bool(data["changed"]),
        )
        return data

    def _iter_local_search_files(self, base_path: Path, *, pattern: str, recursive: bool) -> Iterator[Path]:
        if base_path.is_file():
            yield base_path
            return

        iterator = base_path.rglob(pattern) if recursive else base_path.glob(pattern)
        for candidate in iterator:
            if candidate.is_file() and not candidate.name.startswith("."):
                yield candidate

    def search_text(
        self,
        query: str,
        *,
        path: str | None = None,
        pattern: str = "*",
        recursive: bool = True,
        case_sensitive: bool = False,
        max_results: int | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        """Busca texto en archivos locales fuera del workspace."""
        needle = str(query or "")
        if not needle:
            raise ValueError("query vacia")

        base_path = self.resolve_local_path(path) if str(path or "").strip() else Path.cwd().resolve()
        if not base_path.exists():
            raise FileNotFoundError(f"ruta no encontrada: {base_path}")

        limit = max_results if max_results is not None else self._max_search_results
        limit = max(1, int(limit))
        files_scanned = 0
        files_skipped = 0
        matches: list[dict[str, Any]] = []
        query_cmp = needle if case_sensitive else needle.lower()

        for candidate in self._iter_local_search_files(base_path, pattern=pattern, recursive=recursive):
            try:
                if candidate.stat().st_size > self._max_search_file_bytes:
                    files_skipped += 1
                    continue
                content = candidate.read_text(encoding=encoding, errors="replace")
            except (OSError, ValueError):
                files_skipped += 1
                continue

            files_scanned += 1
            for line_number, line in enumerate(content.splitlines(), start=1):
                line_cmp = line if case_sensitive else line.lower()
                column = line_cmp.find(query_cmp)
                if column < 0:
                    continue
                matches.append(
                    {
                        "path": str(candidate),
                        "line": line_number,
                        "column": column + 1,
                        "line_text": line[:500],
                    }
                )
                if len(matches) >= limit:
                    data = {
                        "base_path": str(base_path),
                        "query": needle,
                        "matches": matches,
                        "count": len(matches),
                        "files_scanned": files_scanned,
                        "files_skipped": files_skipped,
                        "truncated": True,
                    }
                    self._log_action(
                        "file_search_text",
                        base_path=str(base_path),
                        query=needle,
                        count=len(matches),
                        truncated=True,
                    )
                    return data

        data = {
            "base_path": str(base_path),
            "query": needle,
            "matches": matches,
            "count": len(matches),
            "files_scanned": files_scanned,
            "files_skipped": files_skipped,
            "truncated": False,
        }
        self._log_action(
            "file_search_text",
            base_path=str(base_path),
            query=needle,
            count=len(matches),
            truncated=False,
        )
        return data

    @property
    def is_enabled(self) -> bool:
        return self._enabled and not self._kill_switch_active
