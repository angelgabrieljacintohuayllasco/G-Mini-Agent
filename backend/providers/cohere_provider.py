"""
G-Mini Agent — Provider para Cohere (Command A / Command R).
Usa el SDK oficial de Cohere (cliente v2) con streaming.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from loguru import logger

from backend.config import config
from backend.providers import registry
from backend.providers.base import LLMMessage, LLMProvider, LLMResponse, ProviderError, classify_http_status

try:
    import cohere

    HAS_COHERE = True
except ImportError:
    HAS_COHERE = False
    cohere = None  # type: ignore[assignment]


def _usage_tokens(usage: Any) -> tuple[int, int]:
    """SDK 5.x devolvía dicts; 7.x devuelve objetos pydantic (UsageTokens)."""
    tokens = getattr(usage, "tokens", None) if usage else None
    if tokens is None:
        return 0, 0
    if isinstance(tokens, dict):
        return int(tokens.get("input_tokens") or 0), int(tokens.get("output_tokens") or 0)
    return int(getattr(tokens, "input_tokens", 0) or 0), int(getattr(tokens, "output_tokens", 0) or 0)


def _delta_text(event: Any) -> str:
    delta = getattr(event, "delta", None)
    message = getattr(delta, "message", None)
    content = getattr(message, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(getattr(part, "text", "") or "" for part in content)
    return getattr(content, "text", "") or ""


class CohereProvider(LLMProvider):
    """Provider para modelos Command de Cohere."""

    name = "cohere"

    def __init__(self):
        if not HAS_COHERE:
            raise ImportError("El SDK de Cohere no está instalado. Ejecuta: pip install cohere")
        self._client: Any = None
        self._has_key = False
        self._last_usage: dict[str, Any] | None = None

    def _configure(self) -> None:
        vault = config.get("providers", "cohere", "api_key_vault", default="cohere_api")
        api_key = config.get_api_key(vault) or ""
        self._has_key = bool(api_key)
        self._client = cohere.AsyncClientV2(api_key=api_key, timeout=90)

    def is_configured(self) -> bool:
        if self._client is None:
            vault = config.get("providers", "cohere", "api_key_vault", default="cohere_api")
            return bool(config.get_api_key(vault))
        return self._has_key

    def _build_messages(self, messages: list[LLMMessage]) -> list[dict]:
        result: list[dict] = []
        for msg in messages:
            role = msg.role if msg.role in ("system", "assistant") else "user"
            result.append({"role": role, "content": msg.content})
        return result

    def _wrap_error(self, exc: Exception, model: str) -> ProviderError:
        status = getattr(exc, "status_code", None)
        retriable = classify_http_status(status) if isinstance(status, int) else isinstance(exc, (TimeoutError, ConnectionError))
        return ProviderError(self.name, str(exc)[:300], model=model, status=status if isinstance(status, int) else None, retriable=retriable)

    async def generate(
        self,
        messages: list[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = True,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        if not self._client:
            self._configure()
        self._last_usage = None
        try:
            response = self._client.chat_stream(
                model=model,
                messages=self._build_messages(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )
            async for event in response:
                event_type = getattr(event, "type", "")
                if event_type == "content-delta":
                    text = _delta_text(event)
                    if text:
                        yield text
                elif event_type == "message-end":
                    usage = getattr(getattr(event, "delta", None), "usage", None)
                    inp, out = _usage_tokens(usage)
                    self._last_usage = {"input_tokens": inp, "output_tokens": out}
        except Exception as exc:
            logger.warning(f"[cohere] generate error: {exc}")
            raise self._wrap_error(exc, model) from exc

    async def generate_complete(
        self,
        messages: list[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        if not self._client:
            self._configure()
        try:
            response = await self._client.chat(
                model=model,
                messages=self._build_messages(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )
            text = ""
            if response.message and response.message.content:
                text = "".join(getattr(part, "text", "") or "" for part in response.message.content)
            inp, out = _usage_tokens(getattr(response, "usage", None))
            self._last_usage = {"input_tokens": inp, "output_tokens": out}
            return LLMResponse(
                text=text,
                model=model,
                provider="cohere",
                input_tokens=inp,
                output_tokens=out,
                finish_reason=str(getattr(response, "finish_reason", "") or ""),
            )
        except Exception as exc:
            logger.warning(f"[cohere] generate_complete error: {exc}")
            raise self._wrap_error(exc, model) from exc

    async def list_models(self) -> list[str]:
        configured = config.get("providers", "cohere", "models", default=None)
        if configured:
            return configured
        spec = registry.get_spec("cohere")
        return list(spec.default_models) if spec else []

    async def health_check(self) -> bool:
        return self.is_configured()
