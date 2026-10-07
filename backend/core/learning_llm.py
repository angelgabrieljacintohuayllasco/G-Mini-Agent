"""
G-Mini Agent — LLM auxiliar para tareas de fondo (reflexión de memoria, curator).

- Por defecto usa un modelo barato del mismo proveedor que el chat; se puede
  fijar otro con `auxiliary.<tarea>.provider` / `auxiliary.<tarea>.model`.
- Llama al provider directo, sin cadena de fallback: una tarea de fondo no
  debe mandar la conversación a otros proveedores si el elegido falla.
- Tiene timeout, registra el uso en el control de costos y no corre si el
  presupuesto diario o mensual ya está en aviso.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from loguru import logger

from backend.config import config
from backend.providers.base import LLMMessage

# Modelo económico por proveedor (ids verificados en data/models.yaml).
CHEAP_MODELS: dict[str, str] = {
    "google": "gemini-3.5-flash-lite",
    "vertex": "gemini-3.5-flash-lite",
    "openai": "gpt-6-luna",
    "anthropic": "claude-haiku-4-5",
    "xai": "grok-4.3",
    "deepseek": "deepseek-flash",
    "groq": "llama-3.1-8b-instant",
    "mistral": "mistral-small-latest",
    "qwen": "qwen-flash",
    "zai": "glm-5.3-flash",
}


class AuxLLMUnavailable(RuntimeError):
    """No hay proveedor configurado o el presupuesto no lo permite."""


def resolve_aux_binding(section: str) -> tuple[str, str]:
    provider = str(config.get("auxiliary", section, "provider", default="") or "").strip()
    model = str(config.get("auxiliary", section, "model", default="") or "").strip()
    if not provider:
        provider = str(config.get("model_router", "default_provider", default="google") or "google")
    if not model:
        model = CHEAP_MODELS.get(provider) or str(
            config.get("model_router", "default_model", default="gemini-3.8-flash") or ""
        )
    return provider, model


_shared_router: Any = None
_router_lock = threading.Lock()


def set_router(router: Any) -> None:
    """El agente comparte su router (evita re-crear providers y clientes)."""
    global _shared_router
    _shared_router = router


def _get_router() -> Any:
    global _shared_router
    with _router_lock:
        if _shared_router is None:
            from backend.providers.router import ModelRouter

            _shared_router = ModelRouter()
        return _shared_router


async def _budget_allows() -> bool:
    if not config.get("budget", "enabled", default=False):
        return True
    try:
        from backend.core.cost_tracker import get_cost_tracker

        summary = await get_cost_tracker().get_summary()
        status = summary.get("budget_status") or {}
        return all(
            (status.get(scope) or {}).get("state") in (None, "unlimited", "ok")
            for scope in ("daily", "monthly")
        )
    except Exception as exc:
        logger.debug(f"aux LLM: no se pudo revisar el presupuesto ({exc}); no se ejecuta")
        return False


class AuxLLM:
    def __init__(self, section: str = "learning", router: Any = None) -> None:
        self.section = section
        self._router = router

    async def complete(
        self,
        system: str,
        user_text: str,
        *,
        max_tokens: int = 2048,
        timeout: float | None = None,
        session_id: str = "",
    ) -> str:
        provider_name, model = resolve_aux_binding(self.section)
        router = self._router or _get_router()
        provider = router.get_provider(provider_name)
        if provider is None or not provider.is_configured():
            raise AuxLLMUnavailable(f"Proveedor '{provider_name}' no configurado para tareas de fondo.")
        if not await _budget_allows():
            raise AuxLLMUnavailable("Presupuesto en aviso: se omiten las tareas de fondo con LLM.")
        if timeout is None:
            timeout = float(config.get("learning", "reflect_timeout_s", default=45) or 45)

        messages = [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user_text)]
        response = await asyncio.wait_for(
            provider.generate_complete(messages, model, temperature=0.2, max_tokens=max_tokens),
            timeout,
        )
        await self._record_usage(provider_name, response, session_id)
        if not (response.text or "").strip():
            logger.warning(
                f"aux LLM {provider_name}:{model} devolvió texto vacío "
                f"(finish={getattr(response, 'finish_reason', '')!r})"
            )
        return response.text or ""

    async def _record_usage(self, provider_name: str, response: Any, session_id: str) -> None:
        try:
            from backend.core.cost_tracker import get_cost_tracker

            await get_cost_tracker().record_llm_usage(
                session_id=session_id or "background",
                provider=response.provider or provider_name,
                model=response.model,
                source=f"background:{self.section}",
                worker_id=f"aux_{self.section}",
                worker_kind="background",
                input_tokens=int(response.input_tokens or 0),
                output_tokens=int(response.output_tokens or 0),
                estimated=not (response.input_tokens or response.output_tokens),
            )
        except Exception as exc:
            logger.debug(f"aux LLM: no se pudo registrar el uso: {exc}")


def get_aux_llm(section: str = "learning") -> AuxLLM:
    return AuxLLM(section)
