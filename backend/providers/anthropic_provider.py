"""
G-Mini Agent — Provider para Anthropic (Claude).

Usa el SDK nativo con streaming. Los modelos Claude nuevos (Opus 4.7+, Opus 5/5.5,
Sonnet 5/5.5, Fable) rechazan temperature/top_p: se usa thinking adaptativo en su
lugar. Las imágenes se etiquetan con su mime real y los PDF van como bloques
`document`.
"""

from __future__ import annotations

import base64
from typing import Any, AsyncGenerator

import anthropic
from anthropic import AsyncAnthropic
from loguru import logger

from backend.config import config
from backend.providers import registry
from backend.providers.base import LLMMessage, LLMProvider, LLMResponse, ProviderError, classify_http_status

_IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
_SUPPORTED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


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


_SAMPLING_KEYS = ("temperature", "top_p", "top_k")


class AnthropicProvider(LLMProvider):
    """Provider para modelos Claude de Anthropic."""

    name = "anthropic"

    def __init__(self):
        self._client: AsyncAnthropic | None = None
        self._has_key: bool = False
        self._last_usage: dict[str, Any] | None = None

    def _configure(self) -> None:
        vault = config.get("providers", "anthropic", "api_key_vault", default="anthropic_api")
        api_key = config.get_api_key(vault) or ""
        self._has_key = bool(api_key)
        self._client = AsyncAnthropic(api_key=api_key, timeout=90.0)

    def is_configured(self) -> bool:
        if self._client is None:
            vault = config.get("providers", "anthropic", "api_key_vault", default="anthropic_api")
            return bool(config.get_api_key(vault))
        return self._has_key

    def _build_messages(self, messages: list[LLMMessage]) -> tuple[str, list[dict]]:
        system_prompt = ""
        api_messages: list[dict] = []
        for msg in messages:
            if msg.role == "system":
                system_prompt += msg.content + "\n"
                continue

            blocks: list[dict[str, Any]] = []
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})
            for img_b64 in msg.images:
                mime = _sniff_image_mime(img_b64)
                if mime not in _SUPPORTED_IMAGE_MIMES:
                    continue
                blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": img_b64},
                })
            for f in msg.files:
                data_b64 = f.get("data") if isinstance(f, dict) else None
                mime = (f.get("mime_type") if isinstance(f, dict) else None) or ""
                if not data_b64:
                    continue
                if mime == "application/pdf":
                    blocks.append({
                        "type": "document",
                        "source": {"type": "base64", "media_type": mime, "data": data_b64},
                    })
                elif mime in _SUPPORTED_IMAGE_MIMES:
                    blocks.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime, "data": data_b64},
                    })
                else:
                    name = f.get("file_name", "archivo") if isinstance(f, dict) else "archivo"
                    blocks.append({"type": "text", "text": f"[Adjunto no soportado por Anthropic: {name} ({mime})]"})

            if not blocks:
                blocks.append({"type": "text", "text": "(sin contenido)"})
            api_messages.append({"role": msg.role, "content": blocks})
        return system_prompt.strip(), api_messages

    def _request_kwargs(self, model: str, temperature: float, max_tokens: int, kwargs: dict) -> dict:
        # temperature/top_p/top_k y thinking van en extra_body: el SDK 1.x ya no
        # acepta los kwargs de sampling y el 0.76 no conoce thinking "adaptive";
        # así la misma petición sirve con las dos versiones.
        kwargs = dict(kwargs)
        sampling = {key: kwargs.pop(key) for key in _SAMPLING_KEYS if key in kwargs}
        extra_body = dict(kwargs.pop("extra_body", None) or {})
        if registry.anthropic_uses_sampling(model):
            extra_body.update({"temperature": temperature, **sampling})
        else:
            # Opus 4.7+, Sonnet 5.x y Fable rechazan sampling: thinking adaptativo.
            extra_body.setdefault("thinking", {"type": "adaptive"})
        params: dict[str, Any] = {"max_tokens": max_tokens, **kwargs, "extra_body": extra_body}
        return params

    def _wrap_error(self, exc: Exception, model: str) -> ProviderError:
        if isinstance(exc, anthropic.APIStatusError):
            status = getattr(exc, "status_code", None)
            return ProviderError(self.name, str(exc)[:300], model=model, status=status,
                                 retriable=classify_http_status(status))
        if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
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
        if not self._client:
            self._configure()
        system_prompt, api_messages = self._build_messages(messages)
        params = self._request_kwargs(model, temperature, max_tokens, kwargs)
        self._last_usage = None
        try:
            async with self._client.messages.stream(
                model=model,
                messages=api_messages,
                system=system_prompt or "You are a helpful assistant.",
                **params,
            ) as stream_response:
                async for text in stream_response.text_stream:
                    yield text
                final = await stream_response.get_final_message()
                if final and final.usage:
                    self._last_usage = {
                        "input_tokens": final.usage.input_tokens or 0,
                        "output_tokens": final.usage.output_tokens or 0,
                    }
        except Exception as exc:
            logger.warning(f"[anthropic] generate error: {exc}")
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
        system_prompt, api_messages = self._build_messages(messages)
        params = self._request_kwargs(model, temperature, max_tokens, kwargs)
        try:
            response = await self._client.messages.create(
                model=model,
                messages=api_messages,
                system=system_prompt or "You are a helpful assistant.",
                **params,
            )
            text = "".join(getattr(block, "text", "") for block in response.content)
            usage = response.usage
            self._last_usage = {
                "input_tokens": usage.input_tokens if usage else 0,
                "output_tokens": usage.output_tokens if usage else 0,
            }
            return LLMResponse(
                text=text,
                model=model,
                provider="anthropic",
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
                finish_reason=response.stop_reason or "",
            )
        except Exception as exc:
            logger.warning(f"[anthropic] generate_complete error: {exc}")
            raise self._wrap_error(exc, model) from exc

    async def list_models(self) -> list[str]:
        configured = config.get("providers", "anthropic", "models", default=None)
        if configured:
            return configured
        spec = registry.get_spec("anthropic")
        return list(spec.default_models) if spec else []

    async def health_check(self) -> bool:
        return self.is_configured()
