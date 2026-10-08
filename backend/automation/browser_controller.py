"""
G-Mini Agent — BrowserController (Fachada).
Enruta operaciones al backend correcto:
  • ExtensionBridge  → perfiles humanos (Chrome nativo + extensión propia)
  • BrowserUseBridge → perfiles de automatización limpios (browser-use)
Mantiene la misma API pública que las versiones anteriores para que
planner.py no necesite cambios.
"""

from __future__ import annotations

import asyncio
import mimetypes
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from backend.automation.chrome_profiles import ChromeProfileExplorer
from backend.automation.extension_bridge import get_bridge, ExtensionBridge
from backend.automation.browseruse_bridge import BrowserUseBridge
from backend.config import ROOT_DIR, config
from backend.security.virustotal import VirusTotalScanner


def _get_blocked_sites() -> list[str]:
    from backend.config import config

    enabled_value = config.get("agent", "blocked_sites_enabled", default=False)
    enabled = enabled_value if isinstance(enabled_value, bool) else str(enabled_value).strip().lower() in {"1", "true", "yes", "on", "si", "sí"}
    if not enabled:
        return []

    sites = config.get("agent", "blocked_sites", default=None)
    if not isinstance(sites, list):
        sites = config.get("agent", "banned_download_sites", default=[]) or []

    return [str(site).strip().lower() for site in sites if str(site).strip()]


class BrowserController:
    """
    Fachada de automatización de browser.
    • Perfil humano → lanza Chrome con el perfil real del usuario,
      la extensión G-Mini Agent Bridge se conecta por WebSocket al backend.
    • Perfil de automatización → browser-use crea un perfil limpio con Playwright interno.
    """

    def __init__(self) -> None:
        self._chrome = ChromeProfileExplorer()
        self._ext: ExtensionBridge = get_bridge()
        self._bu: BrowserUseBridge = BrowserUseBridge()
        self._virustotal = VirusTotalScanner()
        self._mode: str | None = None          # "human" | "automation" | "extension_fallback"
        self._profile_ref: str | None = None
        self._profile_info: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._downloads_dir = Path.home() / "Downloads" / "G-Mini-Agent"
        self._downloads_dir.mkdir(parents=True, exist_ok=True)
        self._download_log: list[dict[str, Any]] = []
        # Proceso de Chrome lanzado para perfiles humanos
        self._chrome_process = None
        self._session_state: dict[str, Any] = {}

    def _is_chrome_process_running(self) -> bool:
        process = self._chrome_process
        if process is None:
            return False
        try:
            return process.poll() is None
        except Exception:
            return False

    def build_desktop_fallback_hint(
        self,
        *,
        profile_query: str | None = None,
        profile_ref: str | None = None,
        automation: bool = False,
        issue: str = "",
    ) -> dict[str, Any]:
        extension_rel = str(config.get("browser", "extension_path", default="assets/extension") or "assets/extension")
        extension_abs = str((ROOT_DIR / extension_rel).resolve())
        target_profile = str(profile_query or profile_ref or self._profile_ref or "").strip()
        setup_note = (
            "Si necesitas browser_* reales, usa computer use para abrir chrome://extensions, activar modo desarrollador y cargar la carpeta de la extension."
        )
        preferred_open_action = "chrome_open_automation_profile" if automation else "chrome_open_profile"
        desktop_handoff = {
            "kind": "desktop_browser_handoff",
            "status": "ready",
            "automation": automation,
            "backend_required": False,
            "browser_actions_blocked": True,
            "requires_visual_replan": True,
            "preferred_open_action": preferred_open_action,
            "preferred_input_action": "focus_type",
            "preferred_confirm_action": "press",
            "bootstrap_actions": [
                "browser_state",
                "screenshot",
            ],
            "suggested_actions": [
                preferred_open_action,
                "screenshot",
                "click",
                "focus_type",
                "type",
                "press",
                "hotkey",
                "wait",
            ],
            "setup_actions": [
                "chrome://extensions",
                "load_unpacked_extension",
            ],
            "target_profile": target_profile,
            "target_url_strategy": "address_bar",
            "extension_install_url": "chrome://extensions",
            "extension_path": extension_abs,
            "issue": issue.strip(),
        }
        return {
            "kind": "browser_desktop_fallback",
            "automation": automation,
            "profile_query": profile_query or "",
            "profile_ref": profile_ref or "",
            "target_profile": target_profile,
            "issue": issue.strip(),
            "extension_path": extension_abs,
            "extension_relative_path": extension_rel,
            "suggested_actions": [
                "chrome_open_profile" if not automation else "chrome_open_automation_profile",
                "screenshot",
                "click",
                "focus_type",
                "type",
                "press",
                "hotkey",
                "wait",
            ],
            "setup_steps": [
                "Reutiliza o abre Chrome con el perfil deseado.",
                "Toma screenshot() para ubicar la ventana o pestana correcta.",
                "Opera la web con click/focus_type/type/press/wait en vez de browser_*.",
                setup_note,
            ],
            "desktop_handoff": desktop_handoff,
        }

    def _remember_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        readiness = str(payload.get("readiness", "") or "").strip()
        connection = str(payload.get("connection", "") or "").strip()
        backend = str(payload.get("backend", "") or "").strip()
        mode = str(payload.get("mode", self._mode) or "")

        if not backend:
            if readiness == "desktop_fallback_ready":
                backend = "chrome_native_fallback"
            elif connection == "extension" or mode in {"human", "extension_fallback"}:
                backend = "extension"
            elif mode == "automation":
                backend = "browser_use"

        structured_ready = readiness == "structured_browser_ready"
        desktop_ready = readiness == "desktop_fallback_ready"
        payload["backend"] = backend
        payload["structured_browser_ready"] = structured_ready
        payload["desktop_fallback_ready"] = desktop_ready

        state: dict[str, Any] = {
            "mode": mode,
            "profile_ref": str(payload.get("profile_ref", self._profile_ref) or ""),
            "backend": backend,
            "connection": connection,
            "readiness": readiness,
            "structured_browser_ready": structured_ready,
            "desktop_fallback_ready": desktop_ready,
            "hint": str(payload.get("hint", "") or ""),
            "process_running": self._is_chrome_process_running(),
        }
        if isinstance(payload.get("profile"), dict):
            state["profile"] = payload["profile"]
        elif self._profile_info:
            state["profile"] = self._profile_info
        if isinstance(payload.get("recovery_hint"), dict):
            state["recovery_hint"] = payload["recovery_hint"]
            recovery_handoff = payload["recovery_hint"].get("desktop_handoff")
            if isinstance(recovery_handoff, dict):
                state["desktop_handoff"] = recovery_handoff
        if "extension_connected" in payload:
            state["extension_connected"] = bool(payload.get("extension_connected"))
        if "profile_root" in payload:
            state["profile_root"] = str(payload.get("profile_root") or "")
        if isinstance(payload.get("desktop_handoff"), dict):
            state["desktop_handoff"] = payload["desktop_handoff"]

        if desktop_ready and "desktop_handoff" not in state:
            state["desktop_handoff"] = {
                "kind": "desktop_browser_handoff",
                "status": "ready",
                "automation": mode == "automation",
                "backend_required": False,
                "browser_actions_blocked": True,
                "requires_visual_replan": True,
                "preferred_open_action": "chrome_open_automation_profile" if mode == "automation" else "chrome_open_profile",
                "preferred_input_action": "focus_type",
                "preferred_confirm_action": "press",
                "bootstrap_actions": ["browser_state", "screenshot"],
                "suggested_actions": ["screenshot", "click", "focus_type", "type", "press", "wait"],
                "target_profile": state["profile_ref"],
                "target_url_strategy": "address_bar",
                "extension_install_url": "chrome://extensions",
                "extension_path": "",
                "issue": str(payload.get("hint", "") or ""),
            }

        self._session_state = state
        return payload

    # ── Inicialización / Shutdown ────────────────────────────

    async def initialize(self) -> None:
        """Inicializa los bridges disponibles."""
        await self._bu.initialize()
        logger.info(
            f"[BrowserController] Fachada lista — "
            f"extension_bridge=OK, browser_use={'OK' if self._bu.is_available() else 'NO'}"
        )

    async def shutdown(self) -> None:
        """Cierra todos los backends."""
        async with self._lock:
            await self._bu.shutdown()
            # La extensión no se "cierra" — simplemente se desconecta cuando Chrome cierra.
            if self._chrome_process:
                try:
                    self._chrome_process.terminate()
                    self._chrome_process.wait(timeout=5)
                except Exception:
                    try:
                        self._chrome_process.kill()
                    except Exception:
                        pass
                self._chrome_process = None
            self._mode = None
            self._profile_ref = None
            self._profile_info = {}
            self._session_state = {}

    def is_available(self) -> bool:
        """Siempre disponible: al menos la extensión funciona sin deps extra."""
        return True

    # ── Selección de perfil ──────────────────────────────────

    async def ensure_human_profile(
        self,
        query: str | None = None,
        headless: bool = False,
    ) -> dict[str, Any]:
        """
        Abre Chrome con el perfil humano real y espera a que la extensión conecte.
        Si la extensión no está instalada, se puede instalar vía Computer Use (auto-install).
        """
        async with self._lock:
            # Si ya estamos en modo humano con la extensión conectada, reusar
            if self._mode == "human" and self._ext.is_connected:
                logger.debug("[BrowserController] Reutilizando conexión de extensión existente")
                return self._remember_session({
                    "mode": "human",
                    "profile_ref": self._profile_ref,
                    "reused": True,
                    "profile": self._profile_info,
                    "connection": "extension",
                    "readiness": "structured_browser_ready",
                    "structured_browser_ready": True,
                    "desktop_fallback_ready": False,
                })

            # Cerrar sesión previa de automatización si la hay
            await self._bu.shutdown()

            # Descubrir y seleccionar perfil
            profile = self._chrome.select(query)
            if not profile or not isinstance(profile, dict):
                raise RuntimeError(f"No se encontró un perfil de Chrome para: {query}")
            self._profile_info = profile
            self._profile_ref = profile["dir_name"]
            connect_timeout = int(config.get("browser", "extension_connect_timeout_s", default=20))

            # Si la extensión ya está conectada (Chrome ya abierto), listo
            if self._ext.is_connected:
                self._mode = "human"
                logger.info(
                    f"[BrowserController] Extensión ya conectada, "
                    f"perfil={profile['display_name']}"
                )
                return self._remember_session({
                    "mode": "human",
                    "profile_ref": profile["dir_name"],
                    "reused": True,
                    "profile": profile,
                    "connection": "extension",
                    "readiness": "structured_browser_ready",
                    "structured_browser_ready": True,
                    "desktop_fallback_ready": False,
                })

            if self._mode == "human" and self._profile_ref == profile["dir_name"] and self._is_chrome_process_running():
                logger.info(
                    f"[BrowserController] Chrome ya fue lanzado para '{profile['display_name']}', "
                    "esperando conexion de la extension sin relanzar ventana."
                )
                start = time.time()
                while time.time() - start < connect_timeout:
                    if self._ext.is_connected:
                        self._mode = "human"
                        return self._remember_session({
                            "mode": "human",
                            "profile_ref": profile["dir_name"],
                            "reused": True,
                            "profile": profile,
                            "connection": "extension",
                            "extension_connected": True,
                            "readiness": "structured_browser_ready",
                            "structured_browser_ready": True,
                            "desktop_fallback_ready": False,
                        })
                    await asyncio.sleep(0.5)

                hint = self.build_desktop_fallback_hint(
                    profile_query=query,
                    profile_ref=profile["dir_name"],
                    automation=False,
                    issue=(
                        "Chrome se abrio, pero la extension G-Mini Agent Bridge no conecto todavia en el perfil humano."
                    ),
                )
                return self._remember_session({
                    "mode": "human",
                    "profile_ref": profile["dir_name"],
                    "reused": True,
                    "profile": profile,
                    "connection": "pending",
                    "extension_connected": False,
                    "readiness": "desktop_fallback_ready",
                    "structured_browser_ready": False,
                    "desktop_fallback_ready": True,
                    "recovery_hint": hint,
                    "desktop_handoff": hint.get("desktop_handoff"),
                    "hint": (
                        "Chrome ya esta abierto, pero la extension no conecto. "
                        "Puedes seguir con computer use/escritorio o instalar la extension en ese perfil."
                    ),
                })

            # Lanzar Chrome con el perfil seleccionado
            logger.info(
                f"[BrowserController] Lanzando Chrome con perfil "
                f"'{profile['display_name']}' ({profile['dir_name']})"
            )
            process, _ = self._chrome.open(
                query=query,
                url=None,
                new_window=True,
            )
            self._chrome_process = process

            # Esperar a que la extensión conecte
            start = time.time()
            while time.time() - start < connect_timeout:
                if self._ext.is_connected:
                    break
                await asyncio.sleep(0.5)

            if not self._ext.is_connected:
                logger.warning(
                    "[BrowserController] La extensión no conectó en "
                    f"{connect_timeout}s. ¿Está instalada la extensión "
                    "'G-Mini Agent Bridge' en Chrome?"
                )
                self._mode = "human"
                hint = self.build_desktop_fallback_hint(
                    profile_query=query,
                    profile_ref=profile["dir_name"],
                    automation=False,
                    issue=(
                        "Chrome se abrio con el perfil humano, pero la extension G-Mini Agent Bridge no se conecto."
                    ),
                )
                return self._remember_session({
                    "mode": "human",
                    "profile_ref": profile["dir_name"],
                    "reused": False,
                    "profile": profile,
                    "connection": "pending",
                    "extension_connected": False,
                    "readiness": "desktop_fallback_ready",
                    "structured_browser_ready": False,
                    "desktop_fallback_ready": True,
                    "recovery_hint": hint,
                    "desktop_handoff": hint.get("desktop_handoff"),
                    "hint": (
                        "La extension G-Mini Agent Bridge no esta instalada o no conecto. "
                        "Puedes instalarla manualmente: chrome://extensions -> Modo desarrollador -> "
                        f"Cargar descomprimida -> seleccionar carpeta {hint['extension_path']}"
                    ),
                })

            self._mode = "human"
            logger.info(
                f"[BrowserController] Extensión conectada al perfil "
                f"'{profile['display_name']}'"
            )
            return self._remember_session({
                "mode": "human",
                "profile_ref": profile["dir_name"],
                "reused": False,
                "profile": profile,
                "connection": "extension",
                "extension_connected": True,
                "readiness": "structured_browser_ready",
                "structured_browser_ready": True,
                "desktop_fallback_ready": False,
            })

    async def ensure_automation_profile(
        self,
        profile_name: str = "chrome-agent-profile",
        headless: bool = False,
    ) -> dict[str, Any]:
        """
        Crea un perfil limpio de automatización usando browser-use.
        Fallback: si browser-use no está disponible, lanza Chrome con extensión.
        """
        async with self._lock:
            if self._mode == "automation" and self._profile_ref == profile_name:
                state = await self._bu.current_state()
                if state and state.get("active"):
                    return self._remember_session({
                        "mode": "automation",
                        "profile_ref": profile_name,
                        "reused": True,
                        "backend": "browser_use",
                        "readiness": "structured_browser_ready",
                        "structured_browser_ready": True,
                        "desktop_fallback_ready": False,
                    })
            if self._mode == "extension_fallback" and self._profile_ref == profile_name and self._ext.is_connected:
                return self._remember_session({
                    "mode": "automation",
                    "profile_ref": profile_name,
                    "reused": True,
                    "backend": "extension_fallback",
                    "degraded": True,
                    "connection": "extension",
                    "readiness": "structured_browser_ready",
                    "structured_browser_ready": True,
                    "desktop_fallback_ready": False,
                })
            if not self._bu.is_available():
                logger.warning(
                    "[BrowserController] browser-use no disponible. "
                    "Intentando usar la extension conectada como fallback."
                )
                if self._ext.is_connected:
                    self._mode = "extension_fallback"
                    self._profile_ref = profile_name
                    self._profile_info = {"profile_name": profile_name}
                    return self._remember_session({
                        "mode": "automation",
                        "profile_ref": profile_name,
                        "reused": True,
                        "backend": "extension_fallback",
                        "degraded": True,
                        "connection": "extension",
                        "readiness": "structured_browser_ready",
                        "structured_browser_ready": True,
                        "desktop_fallback_ready": False,
                    })
                logger.warning(
                    "[BrowserController] Sin browser-use ni extension conectada. "
                    "Abriendo Chrome nativo para fallback de escritorio."
                )
                process, profile_root = self._chrome.open_automation_profile(profile_name, url=None)
                self._chrome_process = process
                self._mode = "automation"
                self._profile_ref = profile_name
                self._profile_info = {
                    "profile_name": profile_name,
                    "profile_root": str(profile_root),
                }
                hint = self.build_desktop_fallback_hint(
                    profile_query=profile_name,
                    profile_ref=profile_name,
                    automation=True,
                    issue=(
                        "browser-use no esta instalado y no hay una extension conectada. "
                        "Chrome se abrio para continuar con fallback de escritorio/computer use."
                    ),
                )
                return self._remember_session({
                    "mode": "automation",
                    "profile_ref": profile_name,
                    "reused": False,
                    "backend": "chrome_native_fallback",
                    "connection": "desktop",
                    "profile_root": str(profile_root),
                    "readiness": "desktop_fallback_ready",
                    "structured_browser_ready": False,
                    "desktop_fallback_ready": True,
                    "recovery_hint": hint,
                    "desktop_handoff": hint.get("desktop_handoff"),
                    "hint": (
                        "Chrome de automatizacion quedo abierto para operar con escritorio/computer use. "
                        "No uses browser_* hasta que browser-use o la extension esten disponibles."
                    ),
                })

            # Usar browser-use
            result = await self._bu.ensure_automation_profile(
                profile_name=profile_name,
                headless=headless,
                prohibited_domains=_get_blocked_sites(),
                downloads_path=str(self._downloads_dir),
            )
            self._mode = "automation"
            self._profile_ref = profile_name
            result["readiness"] = "structured_browser_ready"
            result["structured_browser_ready"] = True
            result["desktop_fallback_ready"] = False
            return self._remember_session(result)

    async def ensure_any_backend(
        self,
        query: str | None = None,
        headless: bool = False,
    ) -> dict[str, Any]:
        """
        Intenta inicializar un backend de browser en orden de preferencia:
        1. Extensión (perfil humano) — si ya está conectada, usar directamente.
        2. browser-use (perfil de automatización) — si está disponible.
        3. Extensión con espera — lanzar Chrome y esperar conexión de extensión.
        4. Desktop fallback — Chrome abierto sin backend estructurado.
        """
        # 1. Si la extensión ya está conectada, úsala inmediatamente
        if self._ext.is_connected:
            if self._mode == "human":
                return self._session_state or await self.ensure_human_profile(query=query)
            # Extensión conectada pero no en modo human: configurar
            async with self._lock:
                self._mode = "human"
                try:
                    profile = self._chrome.select(query)
                    if profile and isinstance(profile, dict):
                        self._profile_info = profile
                        self._profile_ref = profile["dir_name"]
                except Exception:
                    pass
                return self._remember_session({
                    "mode": "human",
                    "profile_ref": self._profile_ref or "unknown",
                    "reused": True,
                    "connection": "extension",
                    "extension_connected": True,
                    "readiness": "structured_browser_ready",
                    "structured_browser_ready": True,
                    "desktop_fallback_ready": False,
                })

        # 2. Intentar browser-use si está disponible (sin esperar 20s por extensión)
        if self._bu.is_available():
            try:
                result = await self.ensure_automation_profile(headless=headless)
                if result.get("structured_browser_ready"):
                    logger.info("[BrowserController] ensure_any_backend: usando browser-use")
                    return result
            except Exception as exc:
                logger.warning(f"[BrowserController] browser-use falló: {exc}")

        # 3. Intentar extensión (lanzar Chrome y esperar)
        try:
            result = await self.ensure_human_profile(query=query, headless=headless)
            if result.get("structured_browser_ready"):
                return result
            # Si cayó en desktop_fallback, intentar browser-use antes de rendirse
            if result.get("desktop_fallback_ready") and self._bu.is_available():
                try:
                    bu_result = await self.ensure_automation_profile(headless=headless)
                    if bu_result.get("structured_browser_ready"):
                        logger.info("[BrowserController] ensure_any_backend: extensión falló, usando browser-use")
                        return bu_result
                except Exception:
                    pass
            return result
        except Exception as exc:
            logger.error(f"[BrowserController] ensure_any_backend: todos los backends fallaron: {exc}")
            raise

    # ── Router interno ───────────────────────────────────────

    def _is_human(self) -> bool:
        return self._mode in {"human", "extension_fallback"}

    def _require_backend(self) -> str:
        """Verifica que haya un backend activo. Auto-recupera si extensión conectó tras fallback."""
        if self._mode is None:
            raise RuntimeError(
                "No hay sesión de browser activa. "
                "Usa ensure_human_profile() o ensure_automation_profile() primero."
            )
        readiness = str(self._session_state.get("readiness", "") or "").strip().lower()
        if readiness == "desktop_fallback_ready":
            # Auto-recovery: si la extensión se conectó mientras estábamos en fallback,
            # salir del modo fallback y usar la extensión directamente.
            if self._ext.is_connected:
                logger.info("[BrowserController] Auto-recovery: extensión conectada durante desktop_fallback → cambiando a modo human")
                self._mode = "human"
                self._session_state["readiness"] = "structured_browser_ready"
                self._session_state["structured_browser_ready"] = True
                self._session_state["desktop_fallback_ready"] = False
                self._session_state["connection"] = "extension"
                self._session_state["extension_connected"] = True
                return self._mode
            raise RuntimeError(
                "La extensión del navegador no está conectada. "
                "No puedes usar herramientas browser_* (browser_snapshot, browser_click, browser_type, etc.). "
                "En su lugar, usa las herramientas de escritorio: "
                "'screenshot' para ver la pantalla, 'click' para hacer click por coordenadas, "
                "'type' para escribir texto, 'press' para teclas, 'hotkey' para atajos."
            )
        if self._is_human() and not self._ext.is_connected:
            raise RuntimeError(
                "La extensión Chrome no está conectada. "
                "Verifica que Chrome esté abierto con la extensión G-Mini Agent Bridge."
            )
        return self._mode

    # ── Navegación ───────────────────────────────────────────

    async def navigate(self, url: str) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.navigate(url)
        return await self._bu.navigate(url)

    async def go_back(self) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.go_back()
        return await self._bu.go_back()

    async def go_forward(self) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.go_forward()
        return await self._bu.go_forward()

    async def wait_for_load(self, state: str = "domcontentloaded", timeout_ms: int = 30000) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.wait_for_load(state=state, timeout_ms=timeout_ms)
        return await self._bu.wait_for_load(state=state, timeout_ms=timeout_ms)

    # ── Interacción ──────────────────────────────────────────

    async def click(self, selector: str, timeout_ms: int = 10000, force: bool = False) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.click(selector, force=force)
        return await self._bu.click(selector, force=force, timeout_ms=timeout_ms)

    async def type(self, selector: str, text: str, clear: bool = True, timeout_ms: int = 10000) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.type(selector, text, clear=clear)
        return await self._bu.type(selector, text, clear=clear, timeout_ms=timeout_ms)

    async def fill(self, selector: str, text: str, timeout_ms: int = 10000) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.fill(selector, text)
        return await self._bu.fill(selector, text, timeout_ms=timeout_ms)

    async def press(self, key: str) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.press(key)
        return await self._bu.press(key)

    async def scroll(self, direction: str = "down", amount: int = 3) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.scroll(direction=direction, amount=amount)
        return await self._bu.scroll(direction=direction, amount=amount)

    async def hover(self, selector: str, timeout_ms: int = 10000) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.hover(selector)
        return await self._bu.hover(selector, timeout_ms=timeout_ms)

    async def select_option(self, selector: str, value: str, timeout_ms: int = 10000) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.select_option(selector, value)
        return await self._bu.select_option(selector, value, timeout_ms=timeout_ms)

    async def wait_for_selector(self, selector: str, timeout_ms: int = 15000, state: str = "visible") -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.wait_for_selector(selector, timeout_ms=timeout_ms, state=state)
        return await self._bu.wait_for_selector(selector, timeout_ms=timeout_ms, state=state)

    async def remove_overlays(self) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.remove_overlays()
        return await self._bu.remove_overlays()

    # ── Lectura ──────────────────────────────────────────────

    async def extract(self, selector: str = "body", timeout_ms: int = 10000) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.extract(selector)
        return await self._bu.extract(selector, timeout_ms=timeout_ms)

    async def snapshot(self) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.snapshot()
        return await self._bu.snapshot()

    async def evaluate(self, script: str) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.evaluate(script)
        return await self._bu.evaluate(script)

    async def screenshot(self) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.screenshot()
        return await self._bu.screenshot()

    async def get_page_info(self) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.get_page_info()
        return await self._bu.get_page_info()

    # ── Pestañas ─────────────────────────────────────────────

    async def list_tabs(self) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.list_tabs()
        return await self._bu.list_tabs()

    async def switch_tab(self, index: int) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.switch_tab(index)
        return await self._bu.switch_tab(index)

    async def close_tab(self, index: int | None = None) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.close_tab(index)
        return await self._bu.close_tab(index)

    async def new_tab(self, url: str | None = None) -> dict[str, Any]:
        self._require_backend()
        if self._is_human():
            return await self._ext.new_tab(url)
        return await self._bu.new_tab(url)

    # ── Descargas ────────────────────────────────────────────

    async def click_and_wait_for_download(
        self,
        selector: str,
        timeout_ms: int = 30000,
        expected_kind: str = "video",
    ) -> dict[str, Any]:
        """Hace click y espera una descarga. Solo soportado en modo automatización."""
        self._require_backend()
        if self._is_human():
            # En modo humano, hacemos click y luego verificamos la carpeta de descargas
            await self._ext.click(selector)
            # Esperar un poco para que la descarga inicie
            await asyncio.sleep(3)
            result = await self.check_downloads_folder(
                recency_seconds=30,
            )
            if result.get("found") and result.get("files"):
                file_info = result["files"][0]
                details = self._describe_download(
                    Path(file_info["path"]),
                    expected_kind=expected_kind,
                )
                self._download_log.append(details)
                self._download_log = self._download_log[-50:]
                return details
            return {
                "path": "",
                "filename": "",
                "exists": False,
                "hint": "Click ejecutado. Verifica la carpeta de descargas manualmente.",
            }
        else:
            result = await self._bu.click_and_wait_for_download(selector, timeout_ms)
            details = self._describe_download(
                Path(result.get("path", "")),
                expected_kind=expected_kind,
                download_url=result.get("url", ""),
            )
            vt_result = await self.scan_file_with_virustotal(details["path"])
            details["virustotal"] = vt_result
            details["approved_for_use"] = bool(details.get("is_expected")) and bool(
                vt_result.get("trusted")
            )
            self._download_log.append(details)
            self._download_log = self._download_log[-50:]
            return details

    def list_downloads(self, limit: int = 20) -> list[dict[str, Any]]:
        """Lista el log interno de descargas."""
        return self._download_log[-limit:]

    async def check_downloads_folder(
        self,
        expected_ext: str | None = None,
        filename_contains: str | None = None,
        recency_seconds: int = 300,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Verifica archivos descargados recientemente en las carpetas de descargas."""
        now = time.time()
        files: list[dict[str, Any]] = []

        dirs_to_check = [
            self._downloads_dir,
            Path.home() / "Downloads",
        ]

        for dl_dir in dirs_to_check:
            if not dl_dir.exists():
                continue
            for f in dl_dir.iterdir():
                if not f.is_file():
                    continue
                try:
                    stat = f.stat()
                except OSError:
                    continue
                age = now - stat.st_mtime
                if age > recency_seconds:
                    continue
                if expected_ext and f.suffix.lower() != expected_ext.lower():
                    continue
                if filename_contains and filename_contains.lower() not in f.name.lower():
                    continue
                files.append({
                    "path": str(f),
                    "name": f.name,
                    "size_bytes": stat.st_size,
                    "age_seconds": round(age, 1),
                    "suffix": f.suffix.lower(),
                    "dir": str(dl_dir),
                })

        files.sort(key=lambda x: x["age_seconds"])
        files = files[:limit]

        return {
            "found": len(files) > 0,
            "count": len(files),
            "files": files,
            "checked_dirs": [str(d) for d in dirs_to_check],
        }

    # ── VirusTotal ───────────────────────────────────────────

    async def scan_file_with_virustotal(self, file_path: str | Path) -> dict[str, Any]:
        try:
            self._virustotal.refresh_api_key()
            return await self._virustotal.scan_file(file_path)
        except Exception as e:
            logger.warning(f"VirusTotal scan falló: {e}")
            return {
                "configured": self._virustotal.is_configured(),
                "status": "scan_error",
                "trusted": False,
                "file_path": str(file_path),
                "error": str(e),
            }

    # ── Estado ───────────────────────────────────────────────

    async def current_state(self) -> dict[str, Any]:
        """Retorna el estado actual del browser controller."""
        result: dict[str, Any] = {
            "mode": self._mode,
            "profile_ref": self._profile_ref,
            "downloads_dir": str(self._downloads_dir),
            "virustotal_configured": self._virustotal.is_configured(),
        }
        if self._session_state:
            result.update(self._session_state)

        readiness = str(result.get("readiness", "") or "").strip().lower()
        backend = str(result.get("backend", "") or "").strip().lower()

        if readiness == "desktop_fallback_ready":
            result["process_running"] = self._is_chrome_process_running()
            result["browser_actions_blocked"] = True
            handoff = result.get("desktop_handoff")
            if not isinstance(handoff, dict):
                handoff = None
            if handoff:
                handoff = dict(handoff)
                handoff.setdefault("target_profile", str(result.get("profile_ref", "") or ""))
                profile_root = str(result.get("profile_root", "") or "")
                if profile_root:
                    handoff.setdefault("profile_root", profile_root)
                result["desktop_handoff"] = handoff
            return result

        if self._mode in {"human", "extension_fallback"} or backend in {"extension", "extension_fallback"}:
            result["backend"] = "extension"
            result["extension_connected"] = self._ext.is_connected
            if self._ext.is_connected:
                try:
                    info = await self._ext.get_page_info()
                    result["url"] = info.get("url", "")
                    result["title"] = info.get("title", "")
                except Exception:
                    pass
        elif self._mode == "automation" or backend == "browser_use":
            result["backend"] = "browser_use"
            try:
                state = await self._bu.current_state()
                if state and isinstance(state, dict):
                    result.update(state)
            except Exception:
                pass

        return result

    # ── Utilidades privadas ──────────────────────────────────

    def _describe_download(
        self,
        path: Path,
        expected_kind: str = "video",
        download_url: str | None = None,
    ) -> dict[str, Any]:
        suffix = path.suffix.lower()
        mime_type, _ = mimetypes.guess_type(str(path))
        size_bytes = path.stat().st_size if path.exists() else 0
        allowed_video_exts = {".mp4", ".webm", ".m4v", ".mov"}
        suspicious_exts = {".exe", ".msi", ".bat", ".cmd", ".ps1", ".scr", ".zip", ".rar"}
        is_video = suffix in allowed_video_exts or (mime_type or "").startswith("video/")
        is_suspicious = suffix in suspicious_exts
        is_expected = True
        if expected_kind == "video":
            is_expected = is_video and not is_suspicious

        return {
            "path": str(path),
            "filename": path.name,
            "exists": path.exists(),
            "size_bytes": size_bytes,
            "mime_type": mime_type,
            "suffix": suffix,
            "download_url": download_url or "",
            "is_video": is_video,
            "is_suspicious": is_suspicious,
            "is_expected": is_expected,
        }
