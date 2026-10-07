"""
G-Mini Agent — Provider para Google Gemini (AI Studio y Vertex AI).

Un mismo provider sirve para dos ids:
- "google": AI Studio con API key (generativelanguage.googleapis.com).
- "vertex": Vertex AI con credenciales GCP (ADC o cuenta de servicio).

Usa el cliente asíncrono de google-genai con timeout explícito (por defecto no
tiene ninguno) para no colgar el loop.
"""

from __future__ import annotations

import base64
from typing import Any, AsyncGenerator

from loguru import logger

from backend.config import config
from backend.providers import registry
from backend.providers.base import LLMMessage, LLMProvider, LLMResponse, ProviderError, classify_http_status

_DEFAULT_TIMEOUT_MS = 120_000


class GoogleProvider(LLMProvider):
    """Provider para modelos Gemini (AI Studio o Vertex AI)."""

    def __init__(self, provider_name: str = "google", force_backend: str | None = None):
        self.name = provider_name
        self._client = None
        self._backend = force_backend or ""
        self._force_backend = force_backend
        self._ready = False
        self._last_usage: dict[str, Any] | None = None
        # Se configura en el primer uso (detectar el proyecto por ADC tarda ~1 s).

    # ── Configuración ──────────────────────────────────────────────────
    def _config_section(self) -> str:
        return "google" if self.name == "google" else self.name

    def _resolve_backend(self) -> str:
        if self._force_backend:
            return self._force_backend
        return config.get("providers", "google", "backend", default="ai_studio")

    def _configure(self) -> None:
        from google import genai
        from google.genai import types

        self._http_options = types.HttpOptions(
            timeout=int(config.get("providers", self._config_section(), "timeout_ms", default=_DEFAULT_TIMEOUT_MS)),
        )
        self._backend = self._resolve_backend()
        if self.name == "vertex" or self._backend == "vertex_ai":
            from backend.providers import gcp_auth

            settings = gcp_auth.resolve_vertex_settings(
                config.get("providers", self._config_section(), default={}) or {}
            )
            if not settings.project:
                raise ValueError("Vertex AI necesita un proyecto GCP (ADC o cuenta de servicio).")
            credentials = gcp_auth.load_credentials(settings.credentials_file)
            kwargs: dict[str, Any] = {
                "vertexai": True, "project": settings.project, "location": settings.location,
                "http_options": self._http_options,
            }
            if credentials is not None:
                kwargs["credentials"] = credentials
            self._client = genai.Client(**kwargs)
            logger.info(f"[{self.name}] Vertex AI (project={settings.project}, location={settings.location})")
        else:
            vault = config.get("providers", "google", "api_key_vault", default="google_api")
            api_key = config.get_api_key(vault) or ""
            if not api_key:
                raise ValueError("Falta la API key de Google AI Studio.")
            self._client = genai.Client(api_key=api_key, http_options=self._http_options)
            logger.info(f"[{self.name}] AI Studio (API key)")
        self._ready = True

    def _ensure_client(self):
        if not self._ready or self._client is None:
            self._configure()
        return self._client

    def is_configured(self) -> bool:
        try:
            if self.name == "vertex" or self._resolve_backend() == "vertex_ai":
                from backend.providers import gcp_auth

                settings = gcp_auth.resolve_vertex_settings(
                    config.get("providers", self._config_section(), default={}) or {}
                )
                return bool(settings.project)
            vault = config.get("providers", "google", "api_key_vault", default="google_api")
            return bool(config.get_api_key(vault))
        except Exception:
            return False

    def get_backend(self) -> str:
        return self._backend

    # ── Construcción de contenidos ─────────────────────────────────────
    def _build_contents(self, messages: list[LLMMessage]) -> tuple[str | None, list]:
        from google.genai import types

        system_parts: list[str] = []
        contents = []
        for msg in messages:
            if msg.role == "system":
                if msg.content:
                    system_parts.append(msg.content)
                continue
            role = "user" if msg.role == "user" else "model"
            if msg.images or msg.files:
                parts = [types.Part.from_text(text=msg.content)] if msg.content else []
                for img_b64 in msg.images:
                    parts.append(types.Part.from_bytes(data=base64.b64decode(img_b64), mime_type="image/png"))
                for f in msg.files:
                    data_b64 = f.get("data") if isinstance(f, dict) else None
                    mime = f.get("mime_type") if isinstance(f, dict) else None
                    if data_b64 and mime:
                        parts.append(types.Part.from_bytes(data=base64.b64decode(data_b64), mime_type=mime))
                contents.append(types.Content(role=role, parts=parts or [types.Part.from_text(text="(sin contenido)")]))
            else:
                contents.append(types.Content(role=role, parts=[types.Part.from_text(text=msg.content or "")]))
        system_instruction = "\n\n".join(system_parts) if system_parts else None
        return system_instruction, contents

    def _gen_config(self, system_instruction, temperature, max_tokens):
        from google.genai import types

        return types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system_instruction,
        )

    def _wrap_error(self, exc: Exception, model: str) -> ProviderError:
        try:
            from google.genai import errors as genai_errors
        except Exception:
            genai_errors = None
        status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if genai_errors is not None and isinstance(exc, genai_errors.APIError):
            status = getattr(exc, "code", None)
        if isinstance(status, int):
            retriable = classify_http_status(status)
        else:
            status = None
            retriable = isinstance(exc, (TimeoutError, ConnectionError))
        return ProviderError(self.name, str(exc)[:300], model=model, status=status, retriable=retriable)

    def _record_usage(self, response) -> None:
        meta = getattr(response, "usage_metadata", None)
        if meta:
            self._last_usage = {
                "input_tokens": getattr(meta, "prompt_token_count", 0) or 0,
                "output_tokens": getattr(meta, "candidates_token_count", 0) or 0,
                "thinking_tokens": getattr(meta, "thoughts_token_count", 0) or 0,
            }

    async def generate(
        self,
        messages: list[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = True,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        client = self._ensure_client()
        system_instruction, contents = self._build_contents(messages)
        gen_config = self._gen_config(system_instruction, temperature, max_tokens)
        self._last_usage = None
        try:
            stream_iter = await client.aio.models.generate_content_stream(
                model=model, contents=contents, config=gen_config,
            )
            last_finish = None
            async for chunk in stream_iter:
                try:
                    if chunk.candidates and chunk.candidates[0].finish_reason is not None:
                        last_finish = chunk.candidates[0].finish_reason
                except Exception:
                    pass
                if getattr(chunk, "usage_metadata", None):
                    self._record_usage(chunk)
                if chunk.text:
                    yield chunk.text
            if last_finish is not None and "MAX_TOKENS" in str(last_finish):
                logger.warning(f"[{self.name}] generación truncada por MAX_TOKENS (model={model})")
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
        client = self._ensure_client()
        system_instruction, contents = self._build_contents(messages)
        gen_config = self._gen_config(system_instruction, temperature, max_tokens)
        try:
            response = await client.aio.models.generate_content(
                model=model, contents=contents, config=gen_config,
            )
            self._record_usage(response)
            usage = self._last_usage or {}
            return LLMResponse(
                text=response.text or "",
                model=model,
                provider=self.name,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                thinking_tokens=usage.get("thinking_tokens", 0),
            )
        except Exception as exc:
            logger.warning(f"[{self.name}] generate_complete error: {exc}")
            raise self._wrap_error(exc, model) from exc

    async def list_models(self) -> list[str]:
        configured = config.get("providers", self._config_section(), "models", default=None)
        if configured:
            return configured
        spec = registry.get_spec(self.name)
        return list(spec.default_models) if spec else []

    async def health_check(self) -> bool:
        return self.is_configured()
