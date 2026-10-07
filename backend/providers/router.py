"""
G-Mini Agent - Router de proveedores LLM.

Selecciona el provider según config, con fallback real: si el proveedor pedido
falla ANTES de emitir el primer fragmento, se prueba el siguiente de
`model_router.fallback_order`; si falla a mitad de un stream se propaga el error
(no se puede "continuar" una respuesta con otro modelo sin duplicar texto).
Integra cost-aware routing vía CostOptimizer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncGenerator

import yaml
from loguru import logger

from backend.config import ROOT_DIR, config
from backend.providers import registry
from backend.providers.anthropic_provider import AnthropicProvider
from backend.providers.base import (
    LLMMessage,
    LLMProvider,
    LLMProviderUnavailableError,
    LLMResponse,
    ProviderError,
)
from backend.providers.cohere_provider import CohereProvider
from backend.providers.google_provider import GoogleProvider
from backend.providers.openai_compat import OpenAICompatibleProvider

# Compatibilidad: algunos módulos importaban este conjunto.
OPENAI_COMPAT_PROVIDERS = set(registry.openai_compat_ids())


def _build_provider(provider_id: str) -> LLMProvider | None:
    spec = registry.get_spec(provider_id)
    kind = spec.kind if spec else registry.KIND_OPENAI_COMPAT
    if kind == registry.KIND_OPENAI_COMPAT:
        return OpenAICompatibleProvider(provider_id)
    if kind == registry.KIND_ANTHROPIC:
        return AnthropicProvider()
    if kind == registry.KIND_GOOGLE:
        return GoogleProvider("google")
    if kind == registry.KIND_VERTEX:
        return GoogleProvider("vertex", force_backend="vertex_ai")
    if kind == registry.KIND_COHERE:
        return CohereProvider()
    return None


class ModelRouter:
    """Selector de proveedor LLM con fallback automático."""

    def __init__(self):
        self._providers: dict[str, LLMProvider] = {}
        self._last_generation_meta: dict[str, str | bool] = {
            "provider": "",
            "model": "",
            "requested_provider": "",
            "requested_model": "",
            "fallback": False,
        }
        self._last_optimization: Any = None
        self._models_catalog: dict | None = None
        self._initialize_providers()

    # ── Catálogo models.yaml ──────────────────────────────────────
    def _get_models_catalog(self) -> dict:
        if self._models_catalog is not None:
            return self._models_catalog
        try:
            yaml_path = ROOT_DIR / "data" / "models.yaml"
            with open(yaml_path, "r", encoding="utf-8") as f:
                self._models_catalog = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning(f"No se pudo leer models.yaml en router: {exc}")
            self._models_catalog = {}
        return self._models_catalog

    def invalidate_catalog(self) -> None:
        self._models_catalog = None

    def _get_model_meta(self, provider_name: str, model: str) -> dict:
        catalog = self._get_models_catalog()
        provider_models = catalog.get("llm", {}).get(provider_name, {})
        if isinstance(provider_models, dict):
            meta = provider_models.get(model)
            return meta if isinstance(meta, dict) else {}
        return {}

    def _validate_model_for_text_chat(self, provider_name: str, model: str) -> None:
        """Lanza ValueError si el modelo es solo de Live API (voz en tiempo real)."""
        meta = self._get_model_meta(provider_name, model)
        if meta.get("api_method") == "live":
            display = meta.get("display_name", model)
            raise ValueError(
                f"El modelo '{display}' solo funciona con la Live API (voz en tiempo real). "
                f"No soporta chat de texto. Cambia a otro modelo en Ajustes o usa el modo de voz."
            )

    # ── Registro de providers ─────────────────────────────────────
    def _provider_ids(self) -> list[str]:
        ids = list(registry.PROVIDERS)
        # Secciones extra en config.providers con base_url (endpoints propios del usuario).
        for name, section in (config.get("providers", default={}) or {}).items():
            if name not in ids and isinstance(section, dict) and section.get("base_url"):
                ids.append(name)
        return ids

    def _initialize_providers(self) -> None:
        for name in self._provider_ids():
            self._register(name)

    def _register(self, name: str) -> None:
        try:
            provider = _build_provider(name)
        except Exception as exc:
            logger.warning(f"No se pudo inicializar provider {name}: {exc}")
            self._providers.pop(name, None)
            return
        if provider is not None:
            self._providers[name] = provider
            logger.debug(f"Provider inicializado: {name}")

    def reload_provider(self, name: str) -> bool:
        """(Re)crea un provider tras guardar su key o cambiar su config."""
        self._register(name)
        return name in self._providers

    def reload_all(self) -> None:
        self._providers.clear()
        self._initialize_providers()

    def get_provider(self, provider_name: str | None = None) -> LLMProvider | None:
        if provider_name is None:
            provider_name = self.get_current_provider_name()
        return self._providers.get(provider_name)

    def is_available(self, provider_name: str) -> bool:
        provider = self._providers.get(provider_name)
        if provider is None:
            return False
        try:
            return provider.is_configured()
        except Exception:
            return False

    def get_current_model(self) -> str:
        return config.get("model_router", "default_model", default="gemini-3.8-flash")

    def get_current_provider_name(self) -> str:
        return config.get("model_router", "default_provider", default="google")

    def _set_last_generation_meta(
        self,
        *,
        provider: str,
        model: str,
        requested_provider: str,
        requested_model: str,
        fallback: bool,
    ) -> None:
        self._last_generation_meta = {
            "provider": provider,
            "model": model,
            "requested_provider": requested_provider,
            "requested_model": requested_model,
            "fallback": fallback,
        }

    def get_last_generation_meta(self) -> dict[str, str | bool]:
        return dict(self._last_generation_meta)

    def get_last_usage(self) -> dict[str, Any] | None:
        provider = self._providers.get(str(self._last_generation_meta.get("provider") or ""))
        return provider.last_usage() if provider else None

    # ── Fallback ──────────────────────────────────────────────────
    async def _resolve_fallback_candidates(
        self,
        requested_provider: str,
        requested_model: str,
    ) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = {(requested_provider, requested_model)}
        for entry in config.get("model_router", "fallback_order", default=[]) or []:
            parts = str(entry or "").split(":", 1)
            if not parts or not parts[0]:
                continue
            if parts[0] == "local" and len(parts) > 1:
                fb_provider = parts[1]
                fb_model = await self._get_local_model(fb_provider)
                if not fb_model:
                    continue
            else:
                fb_provider = parts[0]
                fb_model = parts[1] if len(parts) > 1 else requested_model
            if not self.is_available(fb_provider):
                continue
            candidate = (fb_provider, fb_model)
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)
        return candidates

    async def _get_local_model(self, provider_name: str) -> str | None:
        """Primer modelo de un provider local; None si no responde (nunca adivina)."""
        provider = self.get_provider(provider_name)
        if not provider:
            return None
        try:
            models = await provider.list_models()
            return models[0] if models else None
        except Exception:
            return None

    # ── Generación ────────────────────────────────────────────────
    async def generate(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        provider_name: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = True,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Streaming con fallback (solo si el fallo ocurre antes del primer fragmento)."""
        model = model or self.get_current_model()
        provider_name = provider_name or self.get_current_provider_name()
        self._validate_model_for_text_chat(provider_name, model)

        attempts: list[tuple[str, str]] = [(provider_name, model)]
        attempts += await self._resolve_fallback_candidates(provider_name, model)
        tried: list[str] = []
        last_error = ""

        for index, (prov_name, prov_model) in enumerate(attempts):
            provider = self.get_provider(prov_name)
            if provider is None:
                last_error = f"provider '{prov_name}' no registrado"
                continue
            tried.append(prov_name)
            self._set_last_generation_meta(
                provider=prov_name,
                model=prov_model,
                requested_provider=provider_name,
                requested_model=model,
                fallback=index > 0,
            )
            yielded_any = False
            try:
                if index > 0:
                    logger.info(f"Fallback a {prov_name}:{prov_model}")
                async for chunk in provider.generate(
                    messages, prov_model, temperature, max_tokens, stream, **kwargs
                ):
                    yielded_any = True
                    yield chunk
                return
            except ProviderError as exc:
                if yielded_any:
                    raise
                last_error = str(exc)
                logger.warning(f"Provider {prov_name} falló antes de responder: {exc}")
            except Exception as exc:  # error inesperado del SDK: tratar como fallo del provider
                if yielded_any:
                    raise
                last_error = str(exc)
                logger.warning(f"Provider {prov_name} falló: {exc}")

        raise LLMProviderUnavailableError(providers_tried=tried, last_error=last_error)

    async def generate_complete(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        provider_name: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Sin streaming, con fallback."""
        model = model or self.get_current_model()
        provider_name = provider_name or self.get_current_provider_name()
        self._validate_model_for_text_chat(provider_name, model)

        attempts: list[tuple[str, str]] = [(provider_name, model)]
        attempts += await self._resolve_fallback_candidates(provider_name, model)
        tried: list[str] = []
        last_error = ""

        for index, (prov_name, prov_model) in enumerate(attempts):
            provider = self.get_provider(prov_name)
            if provider is None:
                last_error = f"provider '{prov_name}' no registrado"
                continue
            tried.append(prov_name)
            try:
                response = await provider.generate_complete(messages, prov_model, **kwargs)
                self._set_last_generation_meta(
                    provider=response.provider or prov_name,
                    model=response.model or prov_model,
                    requested_provider=provider_name,
                    requested_model=model,
                    fallback=index > 0,
                )
                return response
            except Exception as exc:
                last_error = str(exc)
                logger.warning(f"Provider {prov_name} falló (complete): {exc}")

        raise LLMProviderUnavailableError(providers_tried=tried, last_error=last_error)

    async def list_all_models(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for name, provider in self._providers.items():
            try:
                models = await provider.list_models()
                if models:
                    result[name] = models
            except Exception:
                pass
        return result

    async def list_models(self, provider_name: str) -> list[str]:
        provider = self.get_provider(provider_name)
        if provider is None:
            return []
        return await provider.list_models()

    # ── Cost-aware routing ────────────────────────────────────────
    async def generate_cost_aware(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        provider_name: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = True,
        session_id: str = "",
        mode_key: str = "",
        source: str = "agent_loop_stream",
        estimated_input_tokens: int = 0,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        model = model or self.get_current_model()
        provider_name = provider_name or self.get_current_provider_name()
        final_provider, final_model = provider_name, model
        optimization: Any = None
        try:
            from backend.core.cost_optimizer import get_cost_optimizer

            optimization = await get_cost_optimizer().resolve_model(
                requested_provider=provider_name,
                requested_model=model,
                session_id=session_id,
                mode_key=mode_key,
                source=source,
                estimated_input_tokens=estimated_input_tokens,
            )
            if optimization.switched and self.is_available(optimization.provider):
                final_provider, final_model = optimization.provider, optimization.model
                logger.info(
                    f"CostOptimizer switch: {provider_name}:{model} → "
                    f"{final_provider}:{final_model} ({optimization.reason})"
                )
        except Exception as exc:
            logger.debug(f"CostOptimizer no disponible, usando modelo original: {exc}")
        self._last_optimization = optimization

        async for chunk in self.generate(
            messages,
            model=final_model,
            provider_name=final_provider,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            **kwargs,
        ):
            yield chunk

    async def generate_complete_cost_aware(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        provider_name: str | None = None,
        session_id: str = "",
        mode_key: str = "",
        source: str = "agent_loop_complete",
        estimated_input_tokens: int = 0,
        **kwargs,
    ) -> LLMResponse:
        model = model or self.get_current_model()
        provider_name = provider_name or self.get_current_provider_name()
        final_provider, final_model = provider_name, model
        try:
            from backend.core.cost_optimizer import get_cost_optimizer

            optimization = await get_cost_optimizer().resolve_model(
                requested_provider=provider_name,
                requested_model=model,
                session_id=session_id,
                mode_key=mode_key,
                source=source,
                estimated_input_tokens=estimated_input_tokens,
            )
            if optimization.switched and self.is_available(optimization.provider):
                final_provider, final_model = optimization.provider, optimization.model
            self._last_optimization = optimization
        except Exception as exc:
            logger.debug(f"CostOptimizer (complete) no disponible: {exc}")

        return await self.generate_complete(
            messages, model=final_model, provider_name=final_provider, **kwargs
        )

    def get_last_optimization(self) -> Any:
        return getattr(self, "_last_optimization", None)
