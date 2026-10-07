"""
G-Mini Agent — Provider compatible con API OpenAI.

Un solo adaptador para todos los servicios con API compatible OpenAI (OpenAI,
xAI, DeepSeek, Groq, Mistral, Perplexity, OpenRouter, Together, Fireworks,
Cerebras, SambaNova, Moonshot, DashScope, Zhipu, MiniMax, NVIDIA, Hugging Face,
GitHub Models, Azure OpenAI, Ollama, LM Studio, servidores propios…).
"""

from __future__ import annotations

import base64
from typing import Any, AsyncGenerator

import openai
from loguru import logger
from openai import AsyncOpenAI

from backend.config import config
from backend.providers import registry
from backend.providers.base import LLMMessage, LLMProvider, LLMResponse, ProviderError, classify_http_status

# mime por magic bytes (OpenAI rechaza data URLs con el tipo equivocado).
_IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _sniff_image_mime(b64: str) -> str:
    try:
        head = base64.b64decode(b64[:64] + "==", validate=False)
    except Exception:
        return "image/png"
    for sig, mime in _IMAGE_SIGNATURES:
        if head.startswith(sig):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


class OpenAICompatibleProvider(LLMProvider):
    """Provider unificado para servicios con API compatible OpenAI."""

    def __init__(self, provider_name: str):
        self.name = provider_name
        self._client: AsyncOpenAI | None = None
        self._base_url: str = ""
        self._has_key: bool = False
        self._local: bool = False
        self._last_usage: dict[str, Any] | None = None
        spec = registry.get_spec(provider_name)
        self._local = bool(spec and spec.local)
        # El cliente se crea en el primer uso: construir ~25 clientes HTTP y leer
        # el keyring de cada uno al arrancar costaba más de 10 s.

    def _configure(self) -> None:
        spec = registry.get_spec(self.name)
        overrides = config.get("providers", self.name, default={}) or {}
        settings = registry.resolve_provider_settings(self.name, overrides)
        self._base_url = settings["base_url"]
        self._local = bool(spec and spec.local)

        api_key = "not-needed"
        vault = settings.get("api_key_vault", "")
        if vault:
            stored = config.get_api_key(vault)
            if stored:
                api_key = stored
        self._has_key = api_key != "not-needed"

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=self._base_url or None,
            timeout=90.0,
            max_retries=0 if self._local else 2,
        )

    def _ensure_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._configure()
        return self._client

    def is_configured(self) -> bool:
        if self._local:
            return True
        if self._client is None:
            overrides = config.get("providers", self.name, default={}) or {}
            vault = registry.resolve_provider_settings(self.name, overrides).get("api_key_vault", "")
            return bool(vault and config.get_api_key(vault))
        return self._has_key

    def _build_messages(self, messages: list[LLMMessage]) -> list[dict]:
        result = []
        for msg in messages:
            if msg.images:
                content: list[dict[str, Any]] = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                for img_b64 in msg.images:
                    mime = _sniff_image_mime(img_b64)
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{img_b64}"},
                    })
                result.append({"role": msg.role, "content": content or msg.content})
            else:
                result.append({"role": msg.role, "content": msg.content})
        return result

    def _wrap_error(self, exc: Exception, model: str) -> ProviderError:
        if isinstance(exc, openai.APIStatusError):
            status = getattr(exc, "status_code", None)
            return ProviderError(self.name, str(exc)[:300], model=model, status=status,
                                 retriable=classify_http_status(status))
        if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
            return ProviderError(self.name, str(exc)[:300], model=model, retriable=True)
        return ProviderError(self.name, str(exc)[:300], model=model, retriable=False)

    async def generate(
        self,
        messages: list[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = True,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        self._ensure_client()
        api_messages = self._build_messages(messages)
        params = registry.openai_chat_params(self.name, model, temperature=temperature, max_tokens=max_tokens, stream=True)
        params.update(kwargs)
        self._last_usage = None
        try:
            response = await self._client.chat.completions.create(
                model=model, messages=api_messages, stream=True,
                stream_options={"include_usage": True}, **params,
            )
            async for chunk in response:
                if getattr(chunk, "usage", None):
                    self._last_usage = {
                        "input_tokens": chunk.usage.prompt_tokens or 0,
                        "output_tokens": chunk.usage.completion_tokens or 0,
                    }
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as exc:
            logger.warning(f"[{self.name}] generate error: {exc}")
            raise self._wrap_error(exc, model) from exc

    async def generate_complete(
        self,
        messages: list[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        self._ensure_client()
        api_messages = self._build_messages(messages)
        params = registry.openai_chat_params(self.name, model, temperature=temperature, max_tokens=max_tokens, stream=False)
        params.update(kwargs)
        try:
            response = await self._client.chat.completions.create(
                model=model, messages=api_messages, stream=False, **params,
            )
            choice = response.choices[0]
            usage = response.usage
            self._last_usage = {
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
            }
            return LLMResponse(
                text=choice.message.content or "",
                model=model,
                provider=self.name,
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                finish_reason=choice.finish_reason or "",
            )
        except Exception as exc:
            logger.warning(f"[{self.name}] generate_complete error: {exc}")
            raise self._wrap_error(exc, model) from exc

    async def list_models(self) -> list[str]:
        configured = config.get("providers", self.name, "models", default=None)
        if configured is None:
            spec = registry.get_spec(self.name)
            configured = list(spec.default_models) if spec else []
        if configured:
            return configured
        spec = registry.get_spec(self.name)
        if self._local or (spec and spec.supports_model_listing):
            try:
                models = await self._ensure_client().models.list()
                return [m.id for m in models.data]
            except Exception as exc:
                logger.debug(f"[{self.name}] no se pudieron listar modelos: {exc}")
        return []

    async def health_check(self) -> bool:
        try:
            await self._ensure_client().models.list()
            return True
        except Exception:
            return False
