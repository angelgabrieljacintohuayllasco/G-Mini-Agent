"""
G-Mini Agent — Computer Use Sub-Agent (multi-provider).

Sub-agente dedicado a la interacción directa de escritorio. El agente principal
(coordinador) NO ejecuta clicks/typing por sí mismo: delega aquí.

Proveedores soportados (selector provider + model en Settings → Computer Use):
  - google    : Gemini native `computer_use` tool (coords normalizadas 0-1000).
  - anthropic : Claude computer-use beta (`computer_20250124`, coords en píxeles).
  - openai    : Responses API `computer_use_preview` (coords en píxeles).

Solo `google` está verificado en este entorno; anthropic/openai se construyen
según la especificación de cada API y fallan con un error claro si falta el SDK
o la API key correspondiente.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

from backend.config import config
from backend.core.cost_tracker import BudgetLimitExceeded, get_cost_tracker

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class ComputerUseResult:
    status: str  # completed | failed | cancelled | timeout | running
    summary: str = ""
    iterations_used: int = 0
    action_history: list[str] = field(default_factory=list)
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: float = 0.0
    provider: str = ""
    model: str = ""


# Normalización de nombres de tecla estilo xdotool/JS → pyautogui.
_KEY_ALIASES = {
    "return": "enter",
    "enter": "enter",
    "escape": "esc",
    "esc": "esc",
    "delete": "delete",
    "back_space": "backspace",
    "backspace": "backspace",
    "tab": "tab",
    "space": "space",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "page_up": "pageup",
    "pageup": "pageup",
    "page_down": "pagedown",
    "pagedown": "pagedown",
    "home": "home",
    "end": "end",
    "super": "win",
    "meta": "win",
    "cmd": "win",
    "win": "win",
    "control": "ctrl",
    "ctrl": "ctrl",
    "alt": "alt",
    "shift": "shift",
}


def _norm_key(token: str) -> str:
    t = str(token or "").strip().lower().replace(" ", "")
    return _KEY_ALIASES.get(t, t)


def _denormalize(val: float, max_val: int) -> int:
    return int((val / 1000) * max_val)


def _get_monitor_info(target_monitor: int) -> dict[str, int]:
    try:
        import mss
        with mss.mss() as sct:
            if target_monitor == 0:
                mon = sct.monitors[0]
            elif 1 <= target_monitor < len(sct.monitors):
                mon = sct.monitors[target_monitor]
            else:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            return {
                "left": mon["left"],
                "top": mon["top"],
                "width": mon["width"],
                "height": mon["height"],
            }
    except Exception as exc:
        logger.warning(f"No se pudo obtener info de monitor {target_monitor}: {exc}")
        try:
            import pyautogui
            w, h = pyautogui.size()
            return {"left": 0, "top": 0, "width": w, "height": h}
        except Exception:
            return {"left": 0, "top": 0, "width": 1920, "height": 1080}


class ComputerUseAgent:
    def __init__(self, vision: Any, automation: Any):
        self._vision = vision
        self._auto = automation
        self._client = None
        self._provider = ""
        self._cost_tracker = get_cost_tracker()
        self._initialized = False

    # ── Config ──────────────────────────────────────────────────────────
    def _get_provider(self) -> str:
        return str(config.get("computer_use", "provider", default="google") or "google").strip().lower()

    def _get_model(self) -> str:
        return config.get("computer_use", "model", default="gemini-2.5-computer-use-preview-10-2025")

    # ── Init (provider-aware, re-inits si cambia el provider) ────────────
    async def initialize(self) -> None:
        provider = self._get_provider()
        if self._initialized and provider == self._provider and self._client is not None:
            return

        if provider == "google":
            self._client = self._init_google()
        elif provider == "anthropic":
            self._client = self._init_anthropic()
        elif provider == "openai":
            self._client = self._init_openai()
        else:
            raise RuntimeError(
                f"Provider de computer use no soportado: '{provider}'. "
                "Usa google, anthropic u openai."
            )

        self._provider = provider
        self._initialized = True
        logger.info(f"Computer Use Agent inicializado | provider={provider} | model={self._get_model()}")

    def _init_google(self):
        try:
            import google.genai as genai
        except ImportError:
            raise RuntimeError("google-genai no instalado. Requerido para computer use (google).")

        api_key = config.get_api_key("google_api")
        if not api_key:
            raise RuntimeError("API key de Google no configurada (vault: google_api).")

        backend = config.get("providers", "google", "backend", default="ai_studio")
        if backend == "vertex_ai":
            project = config.get("providers", "google", "project_id", default="")
            location = config.get("providers", "google", "location", default="global")
            if not project:
                raise RuntimeError("computer_use con Vertex AI requiere project_id configurado.")
            logger.info(f"Computer Use (google): Vertex AI | project={project} location={location}")
            return genai.Client(vertexai=True, project=project, location=location)
        logger.info("Computer Use (google): AI Studio")
        return genai.Client(api_key=api_key)

    def _init_anthropic(self):
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("paquete 'anthropic' no instalado. Requerido para computer use (anthropic).")
        api_key = config.get_api_key("anthropic_api")
        if not api_key:
            raise RuntimeError("API key de Anthropic no configurada (vault: anthropic_api).")
        return anthropic.Anthropic(api_key=api_key)

    def _init_openai(self):
        try:
            import openai
        except ImportError:
            raise RuntimeError("paquete 'openai' no instalado. Requerido para computer use (openai).")
        api_key = config.get_api_key("openai_api")
        if not api_key:
            raise RuntimeError("API key de OpenAI no configurada (vault: openai_api).")
        return openai.OpenAI(api_key=api_key)

    def _get_system_prompt(self, monitor_info: dict[str, int]) -> str:
        from backend.core.prompt_manager import get_prompt_text
        text, _ = get_prompt_text("computer_use_system", fallback=_FALLBACK_SYSTEM_PROMPT)
        return text.format(
            screen_width=monitor_info["width"],
            screen_height=monitor_info["height"],
        )

    # ── Coordenadas ─────────────────────────────────────────────────────
    def _to_abs(
        self,
        x: float,
        y: float,
        monitor_info: dict[str, int],
        coord_space: str,
        img_dims: tuple[int, int] | None,
    ) -> tuple[int, int]:
        """Convierte coords del modelo → píxeles absolutos del escritorio."""
        if coord_space == "normalized":
            ax = monitor_info["left"] + _denormalize(float(x), monitor_info["width"])
            ay = monitor_info["top"] + _denormalize(float(y), monitor_info["height"])
            return ax, ay
        # pixel: relativas a la imagen que vio el modelo
        img_w, img_h = img_dims or (monitor_info["width"], monitor_info["height"])
        sx = monitor_info["width"] / img_w if img_w else 1.0
        sy = monitor_info["height"] / img_h if img_h else 1.0
        ax = monitor_info["left"] + int(float(x) * sx)
        ay = monitor_info["top"] + int(float(y) * sy)
        return ax, ay

    async def _press_combo(self, key_str: str) -> None:
        parts = [p for p in str(key_str).replace(" ", "").split("+") if p]
        keys = [_norm_key(p) for p in parts]
        if len(keys) > 1:
            await self._auto.hotkey(*keys)
        elif keys:
            await self._auto.press_key(keys[0])

    # ── Ejecutor de acciones común a todos los proveedores ───────────────
    async def _apply_action(
        self,
        kind: str,
        monitor_info: dict[str, int],
        *,
        x: float | None = None,
        y: float | None = None,
        x2: float | None = None,
        y2: float | None = None,
        text: str | None = None,
        key: str | None = None,
        scroll_dir: str | None = None,
        scroll_amount: int = 3,
        coord_space: str = "normalized",
        img_dims: tuple[int, int] | None = None,
        clear: bool = False,
        press_enter: bool = False,
    ) -> str:
        """Ejecuta una acción normalizada vía pc_controller. Devuelve entrada de historial."""
        if kind in ("click", "double_click", "right_click", "move", "type_at") and x is not None and y is not None:
            ax, ay = self._to_abs(x, y, monitor_info, coord_space, img_dims)
        else:
            ax = ay = None

        if kind == "click":
            await self._auto.move_to(ax, ay, duration=0.4)
            await self._auto.click(ax, ay)
            return f"- click({ax}, {ay})"
        if kind == "double_click":
            await self._auto.move_to(ax, ay, duration=0.4)
            await self._auto.double_click(ax, ay)
            return f"- double_click({ax}, {ay})"
        if kind == "right_click":
            await self._auto.move_to(ax, ay, duration=0.4)
            await self._auto.right_click(ax, ay)
            return f"- right_click({ax}, {ay})"
        if kind == "move":
            await self._auto.move_to(ax, ay, duration=0.4)
            return f"- move({ax}, {ay})"
        if kind == "type_at":
            await self._auto.move_to(ax, ay, duration=0.4)
            await self._auto.click(ax, ay)
            await asyncio.sleep(0.3)
            if clear:
                await self._auto.hotkey("ctrl", "a")
                await asyncio.sleep(0.05)
                await self._auto.press_key("delete")
            await self._auto.type_text(text or "", interval=0.03)
            if press_enter:
                await asyncio.sleep(0.1)
                await self._auto.press_key("enter")
            return f"- type_at({ax}, {ay}, {str(text)[:30]!r}, clear={clear}, enter={press_enter})"
        if kind == "type":
            if clear:
                await self._auto.hotkey("ctrl", "a")
                await asyncio.sleep(0.05)
                await self._auto.press_key("delete")
            await self._auto.type_text(text or "", interval=0.03)
            if press_enter:
                await asyncio.sleep(0.1)
                await self._auto.press_key("enter")
            return f"- type({str(text)[:30]!r}, enter={press_enter})"
        if kind == "key":
            await self._press_combo(key or "")
            return f"- key({key})"
        if kind == "scroll":
            amount = int(scroll_amount or 3)
            clicks = amount if (scroll_dir or "down").lower() == "down" else -amount
            sx = sy = None
            if x is not None and y is not None:
                sx, sy = self._to_abs(x, y, monitor_info, coord_space, img_dims)
            await self._auto.scroll(clicks, x=sx, y=sy)
            return f"- scroll(dir={scroll_dir or 'down'}, amount={amount})"
        if kind == "drag":
            sax, say = self._to_abs(x, y, monitor_info, coord_space, img_dims)
            eax, eay = self._to_abs(x2, y2, monitor_info, coord_space, img_dims)
            await self._auto.move_to(sax, say, duration=0.3)
            await self._auto.drag_to(eax, eay, duration=0.6)
            return f"- drag(({sax},{say})→({eax},{eay}))"
        if kind == "wait":
            await asyncio.sleep(float(scroll_amount or 1))
            return "- wait()"
        logger.warning(f"Acción no soportada por el ejecutor: {kind}")
        return f"- {kind}() [no soportada]"

    # ── Captura ──────────────────────────────────────────────────────────
    async def _capture(self, target_monitor: int) -> tuple[str | None, tuple[int, int] | None]:
        try:
            screen = await self._vision.analyze_screen(mode="computer_use", monitor=target_monitor)
        except Exception as exc:
            logger.error(f"Error capturando pantalla: {exc}")
            return None, None
        b64 = screen.get("image_base64")
        dims = screen.get("screen_dimensions") or {}
        img_dims = None
        if dims.get("sent_w") and dims.get("sent_h"):
            img_dims = (int(dims["sent_w"]), int(dims["sent_h"]))
        return b64, img_dims

    # ── Cost tracking común ──────────────────────────────────────────────
    async def _record_usage(
        self,
        result: ComputerUseResult,
        in_tok: int,
        out_tok: int,
        *,
        provider: str,
        model: str,
        iteration: int,
        task: str,
        session_id: str,
        mode_key: str,
        parent_task_limit_usd: float,
    ) -> None:
        result.input_tokens += in_tok
        result.output_tokens += out_tok
        if not session_id:
            return
        usage_event = await self._cost_tracker.record_llm_usage(
            session_id=session_id,
            provider=provider,
            model=model,
            source="computer_use_agent",
            mode_key=mode_key,
            worker_id="computer_use",
            worker_kind="computer_use",
            parent_worker_id="main",
            parent_task_limit_usd=parent_task_limit_usd,
            input_tokens=in_tok,
            output_tokens=out_tok,
            estimated=False,
            metadata={"iteration": iteration, "task": task[:80], "provider": provider},
        )
        result.total_cost_usd += float(usage_event.get("total_cost_usd", 0.0) or 0.0)
        budget_status = usage_event.get("budget_status", {})
        if isinstance(budget_status, dict) and budget_status.get("stop_required"):
            raise BudgetLimitExceeded(
                "\n".join(budget_status.get("alerts", [])) or "Presupuesto excedido."
            )

    async def _emit(self, on_progress, iteration, max_iter, action_name, start_time):
        if not on_progress:
            return
        try:
            await on_progress({
                "iteration": iteration,
                "max_iterations": max_iter,
                "action": action_name,
                "elapsed": time.time() - start_time,
            })
        except Exception:
            pass

    # ── Entry point ──────────────────────────────────────────────────────
    async def execute_task(
        self,
        task: str,
        *,
        target_monitor: int = 0,
        timeout_seconds: float | None = None,
        max_iterations: int | None = None,
        cancel_event: asyncio.Event | None = None,
        on_progress: ProgressCallback | None = None,
        session_id: str = "",
        mode_key: str = "",
        parent_task_limit_usd: float = 0.0,
    ) -> ComputerUseResult:
        await self.initialize()

        if timeout_seconds is None:
            timeout_seconds = float(config.get("computer_use", "timeout_seconds", default=180))
        if max_iterations is None:
            max_iterations = int(config.get("computer_use", "max_iterations", default=30))

        if target_monitor == 0:
            target_monitor = int(config.get("computer_use", "target_monitor", default=0))
            if target_monitor == 0:
                target_monitor = int(config.get("vision", "target_monitor", default=0))

        provider = self._provider
        model = self._get_model()
        monitor_info = _get_monitor_info(target_monitor)

        result = ComputerUseResult(status="running", provider=provider, model=model)
        ctx = {
            "task": task,
            "target_monitor": target_monitor,
            "timeout_seconds": timeout_seconds,
            "max_iterations": max_iterations,
            "stab_delay": float(config.get("computer_use", "stabilization_delay_seconds", default=3)),
            "cancel_event": cancel_event,
            "on_progress": on_progress,
            "session_id": session_id,
            "mode_key": mode_key,
            "parent_task_limit_usd": parent_task_limit_usd,
            "monitor_info": monitor_info,
            "model": model,
        }

        logger.info(
            f"Computer Use iniciando | provider={provider} model={model} | "
            f"task={task[:80]} | monitor={target_monitor} | max_iter={max_iterations}"
        )

        try:
            if provider == "google":
                await self._run_google(result, ctx)
            elif provider == "anthropic":
                await self._run_anthropic(result, ctx)
            elif provider == "openai":
                await self._run_openai(result, ctx)
        except BudgetLimitExceeded as exc:
            result.status = "failed"
            result.error = f"Presupuesto excedido: {exc}"
            logger.warning(f"Computer Use abortado por presupuesto: {exc}")
        except Exception as exc:
            result.status = "failed"
            result.error = str(exc)
            logger.error(f"Computer Use error inesperado ({provider}): {exc}")

        if result.status == "running":
            result.status = "timeout"
            result.summary = result.summary or f"Alcanzó {max_iterations} iteraciones sin completar."
        result.iterations_used = len(result.action_history)
        return result

    # ── GOOGLE: Gemini native computer_use (coords normalizadas 0-1000) ──
    async def _run_google(self, result: ComputerUseResult, ctx: dict) -> None:
        from google.genai import types

        monitor_info = ctx["monitor_info"]
        system_prompt = self._get_system_prompt(monitor_info)
        # Excluir funciones de navegador: este sub-agente controla el ESCRITORIO de Windows,
        # no un navegador. Si no se excluyen, el modelo (entorno BROWSER) llama a
        # `open_web_browser` como primer paso → abre un navegador real que tapa el escritorio
        # y desvía todos los clicks siguientes. Excluyéndolas, usa el menú Inicio / iconos.
        gen_config = types.GenerateContentConfig(
            tools=[types.Tool(computer_use=types.ComputerUse(
                environment=types.Environment.ENVIRONMENT_BROWSER,
                excluded_predefined_functions=["open_web_browser", "search", "navigate"],
            ))]
        )
        action_history = result.action_history
        start_time = time.time()
        last_hash = ""
        stagnation = 0

        for iteration in range(1, ctx["max_iterations"] + 1):
            if time.time() - start_time > ctx["timeout_seconds"]:
                result.status = "timeout"
                result.summary = f"Timeout tras {iteration - 1} iteraciones."
                return
            if ctx["cancel_event"] and ctx["cancel_event"].is_set():
                result.status = "cancelled"
                result.summary = f"Cancelado en iteración {iteration}"
                return

            image_b64, _ = await self._capture(ctx["target_monitor"])
            if not image_b64:
                await asyncio.sleep(1)
                continue

            h = hashlib.md5(image_b64[:500].encode()).hexdigest()
            stagnation = stagnation + 1 if h == last_hash else 0
            last_hash = h

            screenshot_part = types.Part(inline_data=types.Blob(mime_type="image/png", data=base64.b64decode(image_b64)))
            history_text = ("Acciones previas:\n" + "\n".join(action_history)) if action_history else "Primer paso."
            step = "Ejecuta el siguiente paso lógico."
            if stagnation >= 3:
                step = "ATENCIÓN: la pantalla no cambia. Cambia de estrategia (otra ubicación o método)."

            try:
                response = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self._client.models.generate_content(
                        model=ctx["model"],
                        contents=[system_prompt, f"Objetivo: {ctx['task']}", history_text, step, screenshot_part],
                        config=gen_config,
                    ),
                )
            except Exception as exc:
                logger.error(f"Error llamando Gemini computer use: {exc}")
                await asyncio.sleep(2)
                continue

            if ctx["cancel_event"] and ctx["cancel_event"].is_set():
                result.status = "cancelled"
                result.summary = f"Cancelado en iteración {iteration}"
                return

            usage = getattr(response, "usage_metadata", None)
            in_tok = (getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
            out_tok = (getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
            await self._record_usage(result, in_tok, out_tok, provider="google", model=ctx["model"],
                                     iteration=iteration, task=ctx["task"], session_id=ctx["session_id"],
                                     mode_key=ctx["mode_key"], parent_task_limit_usd=ctx["parent_task_limit_usd"])

            # Parse function_call defensivamente: candidates/content/parts pueden venir None
            function_call = None
            text_response = ""
            try:
                candidates = getattr(response, "candidates", None) or []
                if candidates:
                    content = getattr(candidates[0], "content", None)
                    parts = getattr(content, "parts", None) or []
                    for part in parts:
                        fc = getattr(part, "function_call", None)
                        if fc and getattr(fc, "name", ""):
                            function_call = fc
                            break
                        # Capturar texto si el modelo responde sin function_call
                        part_text = getattr(part, "text", None)
                        if part_text:
                            text_response += str(part_text)
            except Exception as exc:
                logger.warning(f"No se pudo parsear respuesta de Gemini CU: {exc}")

            if function_call is None:
                # Si el modelo responde con texto pero sin function_call,
                # significa que considera la tarea terminada (no tiene más acciones).
                # Esto es equivalente a llamar done() implícitamente.
                if text_response.strip():
                    logger.info(f"Computer Use: modelo respondió con texto sin acción → tarea completada: {text_response[:120]}")
                    result.status = "completed"
                    result.summary = text_response.strip()[:200] or f"Tarea completada en {iteration} iteraciones."
                    action_history.append(f"- [texto] {text_response.strip()[:80]}")
                    return
                logger.warning("Gemini CU no devolvió ni acción ni texto; reintentando")
                await asyncio.sleep(1.5)
                continue

            name = function_call.name
            args = dict(function_call.args or {})
            logger.info(f"Computer Use (google) acción: {name} {args}")

            if name == "done":
                result.status = "completed"
                result.summary = f"Tarea completada en {iteration} iteraciones."
                action_history.append("- done()")
                return

            exec_note = await self._apply_google_action(name, args, monitor_info)
            # Historial en el espacio del MODELO (normalizado 0-1000), como en la implementación
            # de referencia. Mostrar píxeles absolutos del escritorio confunde el razonamiento
            # espacial del modelo (cree que clickeó en 0-1000). Si el ejecutor marcó un problema
            # ([ignorado]/[no soportada]/[error]), se conserva esa nota para que el modelo adapte.
            if exec_note and "[" in exec_note:
                action_history.append(exec_note)
            else:
                action_history.append(f"- {name}({args})")

            await self._emit(ctx["on_progress"], iteration, ctx["max_iterations"], name, start_time)
            await asyncio.sleep(ctx["stab_delay"])

    async def _apply_google_action(self, name: str, args: dict, monitor_info) -> str:
        """Mapea las funciones predefinidas de Gemini computer use → ejecutor común."""
        if name in ("click_at", "left_click_at"):
            return await self._apply_action("click", monitor_info, x=args.get("x", 0), y=args.get("y", 0), coord_space="normalized")
        if name == "double_click_at":
            return await self._apply_action("double_click", monitor_info, x=args.get("x", 0), y=args.get("y", 0), coord_space="normalized")
        if name == "right_click_at":
            return await self._apply_action("right_click", monitor_info, x=args.get("x", 0), y=args.get("y", 0), coord_space="normalized")
        if name in ("move_mouse", "hover_at"):
            return await self._apply_action("move", monitor_info, x=args.get("x", 0), y=args.get("y", 0), coord_space="normalized")
        if name == "type_text_at":
            return await self._apply_action(
                "type_at", monitor_info, x=args.get("x", 0), y=args.get("y", 0),
                text=str(args.get("text", "")),
                clear=bool(args.get("clear_before_typing", False)),
                press_enter=bool(args.get("press_enter", False)),
                coord_space="normalized",
            )
        if name == "type_text":
            return await self._apply_action("type", monitor_info, text=str(args.get("text", "")),
                                            press_enter=bool(args.get("press_enter", False)))
        if name in ("press_key", "key_combination"):
            keys = args.get("keys") or args.get("key") or ""
            if isinstance(keys, (list, tuple)):
                keys = "+".join(str(k) for k in keys)
            return await self._apply_action("key", monitor_info, key=str(keys))
        if name == "scroll_document":
            return await self._apply_action("scroll", monitor_info, scroll_dir=str(args.get("direction", "down")), scroll_amount=5)
        if name in ("scroll", "scroll_at"):
            return await self._apply_action(
                "scroll", monitor_info, x=args.get("x"), y=args.get("y"),
                scroll_dir=str(args.get("direction", "down")),
                scroll_amount=int(args.get("magnitude", args.get("amount", 3)) or 3),
                coord_space="normalized",
            )
        if name == "drag_and_drop":
            return await self._apply_action(
                "drag", monitor_info, x=args.get("x", 0), y=args.get("y", 0),
                x2=args.get("destination_x", 0), y2=args.get("destination_y", 0), coord_space="normalized",
            )
        if name == "wait_5_seconds":
            await asyncio.sleep(5)
            return "- wait_5_seconds()"
        if name == "go_back":
            await self._auto.hotkey("alt", "left")
            return "- go_back()"
        if name == "go_forward":
            await self._auto.hotkey("alt", "right")
            return "- go_forward()"
        if name == "open_web_browser":
            # Excluida a nivel de API; si llega igualmente NO abrir navegador: en control de
            # escritorio abrir un navegador tapa la pantalla y desvía los clicks. No-op.
            logger.info("Computer Use: open_web_browser ignorado (control de escritorio, no se abre navegador).")
            return "- open_web_browser() [ignorado: control de escritorio]"
        if name in ("navigate", "search"):
            return await self._open_browser_action(name, args)
        logger.warning(f"Acción Gemini CU no soportada: {name}")
        return f"- {name}({args}) [no soportada]"

    async def _open_browser_action(self, name: str, args: dict) -> str:
        """Best-effort: abre el navegador / navega / busca (funciones de browser de Gemini CU)."""
        import subprocess
        try:
            if name == "navigate":
                url = str(args.get("url", "")).strip()
                if url:
                    subprocess.Popen(f'start "" "{url}"', shell=True)
                    return f"- navigate({url})"
            if name == "search":
                q = str(args.get("query", "")).strip()
                url = f"https://www.google.com/search?q={q.replace(' ', '+')}"
                subprocess.Popen(f'start "" "{url}"', shell=True)
                return f"- search({q})"
            subprocess.Popen('start "" chrome', shell=True)
            return "- open_web_browser()"
        except Exception as exc:
            logger.warning(f"open_browser_action falló: {exc}")
            return f"- {name}() [error]"

    # ── ANTHROPIC: Claude computer-use beta (coords en píxeles) ──────────
    async def _run_anthropic(self, result: ComputerUseResult, ctx: dict) -> None:
        monitor_info = ctx["monitor_info"]
        system_prompt = self._get_system_prompt(monitor_info)
        tool = {
            "type": "computer_20250124",
            "name": "computer",
            "display_width_px": monitor_info["width"],
            "display_height_px": monitor_info["height"],
            "display_number": 1,
        }
        action_history = result.action_history
        start_time = time.time()

        image_b64, img_dims = await self._capture(ctx["target_monitor"])
        if not image_b64:
            result.status = "failed"
            result.error = "No se pudo capturar la pantalla inicial."
            return
        messages: list[dict[str, Any]] = [{
            "role": "user",
            "content": [
                {"type": "text", "text": f"Objetivo: {ctx['task']}"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
            ],
        }]

        for iteration in range(1, ctx["max_iterations"] + 1):
            if time.time() - start_time > ctx["timeout_seconds"]:
                result.status = "timeout"
                return
            if ctx["cancel_event"] and ctx["cancel_event"].is_set():
                result.status = "cancelled"
                return

            try:
                response = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self._client.beta.messages.create(
                        model=ctx["model"],
                        max_tokens=1024,
                        system=system_prompt,
                        tools=[tool],
                        betas=["computer-use-2025-01-24"],
                        messages=messages,
                    ),
                )
            except Exception as exc:
                result.status = "failed"
                result.error = f"Anthropic API: {exc}"
                return

            usage = getattr(response, "usage", None)
            await self._record_usage(result, getattr(usage, "input_tokens", 0) or 0,
                                     getattr(usage, "output_tokens", 0) or 0, provider="anthropic",
                                     model=ctx["model"], iteration=iteration, task=ctx["task"],
                                     session_id=ctx["session_id"], mode_key=ctx["mode_key"],
                                     parent_task_limit_usd=ctx["parent_task_limit_usd"])

            tool_uses = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
            messages.append({"role": "assistant", "content": [b.model_dump() if hasattr(b, "model_dump") else b for b in response.content]})

            if not tool_uses or getattr(response, "stop_reason", "") == "end_turn":
                result.status = "completed"
                result.summary = f"Tarea completada en {iteration} iteraciones."
                return

            tool_results = []
            for tu in tool_uses:
                action_input = dict(getattr(tu, "input", {}) or {})
                act = str(action_input.get("action", ""))
                logger.info(f"Computer Use (anthropic) acción: {act} {action_input}")
                entry = await self._apply_anthropic_action(act, action_input, monitor_info, img_dims)
                action_history.append(entry)
                await self._emit(ctx["on_progress"], iteration, ctx["max_iterations"], act, start_time)
                await asyncio.sleep(ctx["stab_delay"])

                image_b64, img_dims = await self._capture(ctx["target_monitor"])
                tr: dict[str, Any] = {"type": "tool_result", "tool_use_id": tu.id}
                if image_b64:
                    tr["content"] = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}}]
                else:
                    tr["content"] = [{"type": "text", "text": "screenshot no disponible"}]
                tool_results.append(tr)
            messages.append({"role": "user", "content": tool_results})

    async def _apply_anthropic_action(self, act, inp, monitor_info, img_dims) -> str:
        coord = inp.get("coordinate") or [None, None]
        cx, cy = (coord + [None, None])[:2]
        if act in ("left_click", "middle_click"):
            return await self._apply_action("click", monitor_info, x=cx, y=cy, coord_space="pixel", img_dims=img_dims)
        if act == "right_click":
            return await self._apply_action("right_click", monitor_info, x=cx, y=cy, coord_space="pixel", img_dims=img_dims)
        if act in ("double_click", "triple_click"):
            return await self._apply_action("double_click", monitor_info, x=cx, y=cy, coord_space="pixel", img_dims=img_dims)
        if act in ("mouse_move",):
            return await self._apply_action("move", monitor_info, x=cx, y=cy, coord_space="pixel", img_dims=img_dims)
        if act == "left_click_drag":
            start = inp.get("start_coordinate") or [cx, cy]
            return await self._apply_action("drag", monitor_info, x=start[0], y=start[1], x2=cx, y2=cy,
                                            coord_space="pixel", img_dims=img_dims)
        if act == "type":
            return await self._apply_action("type", monitor_info, text=str(inp.get("text", "")))
        if act in ("key", "hold_key"):
            return await self._apply_action("key", monitor_info, key=str(inp.get("text", "")))
        if act == "scroll":
            return await self._apply_action("scroll", monitor_info, x=cx, y=cy,
                                            scroll_dir=str(inp.get("scroll_direction", "down")),
                                            scroll_amount=int(inp.get("scroll_amount", 3)),
                                            coord_space="pixel", img_dims=img_dims)
        if act == "wait":
            return await self._apply_action("wait", monitor_info, scroll_amount=int(inp.get("duration", 1)))
        if act in ("screenshot", "cursor_position"):
            return f"- {act}()"
        return f"- {act}({inp}) [no soportada]"

    # ── OPENAI: Responses API computer_use_preview (coords en píxeles) ───
    async def _run_openai(self, result: ComputerUseResult, ctx: dict) -> None:
        monitor_info = ctx["monitor_info"]
        system_prompt = self._get_system_prompt(monitor_info)
        tools = [{
            "type": "computer_use_preview",
            "display_width": monitor_info["width"],
            "display_height": monitor_info["height"],
            "environment": "windows",
        }]
        action_history = result.action_history
        start_time = time.time()

        image_b64, img_dims = await self._capture(ctx["target_monitor"])
        if not image_b64:
            result.status = "failed"
            result.error = "No se pudo capturar la pantalla inicial."
            return

        input_items: list[dict[str, Any]] = [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": f"{system_prompt}\n\nObjetivo: {ctx['task']}"},
                {"type": "input_image", "image_url": f"data:image/png;base64,{image_b64}"},
            ],
        }]
        prev_response_id = None

        for iteration in range(1, ctx["max_iterations"] + 1):
            if time.time() - start_time > ctx["timeout_seconds"]:
                result.status = "timeout"
                return
            if ctx["cancel_event"] and ctx["cancel_event"].is_set():
                result.status = "cancelled"
                return

            try:
                kwargs = dict(model=ctx["model"], tools=tools, input=input_items, truncation="auto")
                if prev_response_id:
                    kwargs["previous_response_id"] = prev_response_id
                response = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: self._client.responses.create(**kwargs)
                )
            except Exception as exc:
                result.status = "failed"
                result.error = f"OpenAI API: {exc}"
                return

            prev_response_id = getattr(response, "id", None)
            usage = getattr(response, "usage", None)
            await self._record_usage(result, getattr(usage, "input_tokens", 0) or 0,
                                     getattr(usage, "output_tokens", 0) or 0, provider="openai",
                                     model=ctx["model"], iteration=iteration, task=ctx["task"],
                                     session_id=ctx["session_id"], mode_key=ctx["mode_key"],
                                     parent_task_limit_usd=ctx["parent_task_limit_usd"])

            calls = [o for o in getattr(response, "output", []) if getattr(o, "type", "") == "computer_call"]
            if not calls:
                result.status = "completed"
                result.summary = f"Tarea completada en {iteration} iteraciones."
                return

            input_items = []
            for call in calls:
                action = getattr(call, "action", None)
                action_d = action.model_dump() if hasattr(action, "model_dump") else dict(action or {})
                logger.info(f"Computer Use (openai) acción: {action_d.get('type')} {action_d}")
                entry = await self._apply_openai_action(action_d, monitor_info, img_dims)
                action_history.append(entry)
                await self._emit(ctx["on_progress"], iteration, ctx["max_iterations"], action_d.get("type", ""), start_time)
                await asyncio.sleep(ctx["stab_delay"])

                image_b64, img_dims = await self._capture(ctx["target_monitor"])
                output_item: dict[str, Any] = {
                    "type": "computer_call_output",
                    "call_id": getattr(call, "call_id", getattr(call, "id", "")),
                    "output": {"type": "computer_screenshot", "image_url": f"data:image/png;base64,{image_b64}"},
                }
                pending = getattr(call, "pending_safety_checks", None)
                if pending:
                    output_item["acknowledged_safety_checks"] = [
                        (p.model_dump() if hasattr(p, "model_dump") else p) for p in pending
                    ]
                input_items.append(output_item)

    async def _apply_openai_action(self, a: dict, monitor_info, img_dims) -> str:
        t = str(a.get("type", ""))
        x, y = a.get("x"), a.get("y")
        if t == "click":
            btn = str(a.get("button", "left"))
            kind = "right_click" if btn == "right" else "click"
            return await self._apply_action(kind, monitor_info, x=x, y=y, coord_space="pixel", img_dims=img_dims)
        if t == "double_click":
            return await self._apply_action("double_click", monitor_info, x=x, y=y, coord_space="pixel", img_dims=img_dims)
        if t == "move":
            return await self._apply_action("move", monitor_info, x=x, y=y, coord_space="pixel", img_dims=img_dims)
        if t == "type":
            return await self._apply_action("type", monitor_info, text=str(a.get("text", "")))
        if t == "keypress":
            keys = a.get("keys") or []
            return await self._apply_action("key", monitor_info, key="+".join(str(k) for k in keys))
        if t == "scroll":
            sy = int(a.get("scroll_y", 0) or 0)
            direction = "down" if sy >= 0 else "up"
            return await self._apply_action("scroll", monitor_info, x=x, y=y, scroll_dir=direction,
                                            scroll_amount=max(1, abs(sy) // 100 or 3), coord_space="pixel", img_dims=img_dims)
        if t == "drag":
            path = a.get("path") or []
            if len(path) >= 2:
                return await self._apply_action("drag", monitor_info, x=path[0].get("x"), y=path[0].get("y"),
                                                x2=path[-1].get("x"), y2=path[-1].get("y"),
                                                coord_space="pixel", img_dims=img_dims)
            return "- drag() [path inválido]"
        if t == "wait":
            return await self._apply_action("wait", monitor_info, scroll_amount=1)
        if t == "screenshot":
            return "- screenshot()"
        return f"- {t}({a}) [no soportada]"


_FALLBACK_SYSTEM_PROMPT = """Estás operando un escritorio Windows. Tu tarea es alcanzar el objetivo indicado por el usuario ejecutando una secuencia de acciones.

Reglas importantes:
- Para abrir un icono en el escritorio (carpeta, archivo, programa), DEBES usar doble click.
- Para hacer clic en un botón o seleccionar un elemento, usa un clic simple.
- Para escribir texto, escribe en el campo activo o haz click primero y luego escribe.
- Confirma diálogos o entradas con la tecla 'enter'.
- Algunos botones pueden parecer deshabilitados (grises) pero aún son clickeables. Si un botón como 'Siguiente' o 'Aceptar' es el paso lógico, intenta hacer clic.
- **IMPORTANTE: Cuando la tarea esté completamente terminada, DEBES llamar a la función `done` inmediatamente. No ejecutes acciones adicionales después de completar el objetivo.**
- Resolución de pantalla actual: {screen_width}x{screen_height} pixels.
- Antes de hacer clic, identifica visualmente el centro exacto del elemento objetivo.
- Si un clic no funciona, reanaliza la pantalla y ajusta las coordenadas.
- Operas sobre el monitor que se te asignó; trabaja solo con lo que ves en esa captura.
- Una vez que el objetivo se haya cumplido, llama a `done` de inmediato."""
