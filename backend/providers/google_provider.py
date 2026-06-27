"""
G-Mini Agent — Provider para Google (Gemini).
Soporta dos backends:
  - ai_studio: usa API key (generativelanguage.googleapis.com)
  - vertex_ai: usa credenciales GCP (aiplatform.googleapis.com), consume créditos de Google Cloud
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

from loguru import logger

from backend.providers.base import LLMProvider, LLMMessage, LLMResponse
from backend.config import config


class GoogleProvider(LLMProvider):
    """Provider para modelos Gemini de Google."""

    name = "google"

    def __init__(self):
        self._client = None
        self._backend = "ai_studio"
        self._configure()

    def _configure(self) -> None:
        try:
            from google import genai

            self._backend = config.get("providers", "google", "backend", default="ai_studio")

            if self._backend == "vertex_ai":
                self._configure_vertex(genai)
            else:
                self._configure_ai_studio(genai)

        except Exception as e:
            self._client = None
            raise

    def _configure_ai_studio(self, genai) -> None:
        vault = config.get("providers", "google", "api_key_vault", default="google_api")
        api_key = config.get_api_key(vault) or ""

        if not api_key:
            raise ValueError("No API key configured for Google AI Studio. Set it in Settings > API Keys.")

        self._client = genai.Client(api_key=api_key)
        logger.info("[google] Backend: AI Studio (API key)")

    def _configure_vertex(self, genai) -> None:
        project_id = config.get("providers", "google", "project_id", default="")
        location = config.get("providers", "google", "location", default="us-central1")
        credentials_file = config.get("providers", "google", "credentials_file", default="")

        if not project_id:
            raise ValueError(
                "Vertex AI requires a GCP project_id. "
                "Set it in Settings > Google > Project ID."
            )

        if credentials_file:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_file

        self._client = genai.Client(
            vertexai=True,
            project=project_id,
            location=location,
        )
        logger.info(f"[google] Backend: Vertex AI (project={project_id}, location={location})")

    def get_backend(self) -> str:
        return self._backend

    def _build_contents(self, messages: list[LLMMessage]) -> tuple[str | None, list]:
        """
        Convierte LLMMessage al formato Google genai.
        Retorna (system_instruction, contents).
        """
        from google.genai import types

        system_instruction = None
        contents = []

        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content
                continue

            role = "user" if msg.role == "user" else "model"

            if msg.images or msg.files:
                import base64
                parts = [types.Part.from_text(text=msg.content)]
                for img_b64 in msg.images:
                    img_bytes = base64.b64decode(img_b64)
                    parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
                # Adjuntos no-imagen (video/audio/pdf/doc): bytes inline con su mime real.
                # Part.from_bytes soporta video/mp4, audio/*, application/pdf en Vertex y AI Studio.
                for f in msg.files:
                    data_b64 = f.get("data") if isinstance(f, dict) else None
                    mime = f.get("mime_type") if isinstance(f, dict) else None
                    if not data_b64 or not mime:
                        continue
                    parts.append(types.Part.from_bytes(data=base64.b64decode(data_b64), mime_type=mime))
                contents.append(types.Content(role=role, parts=parts))
            else:
                contents.append(types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=msg.content)],
                ))

        return system_instruction, contents

    async def generate(
        self,
        messages: list[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = True,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Streaming generation con Gemini."""
        if not self._client:
            self._configure()

        from google.genai import types
        import asyncio

        system_instruction, contents = self._build_contents(messages)

        gen_config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system_instruction,
        )

        try:
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[str | None] = asyncio.Queue()

            def _sync_stream():
                last_finish = None
                try:
                    response = self._client.models.generate_content_stream(
                        model=model,
                        contents=contents,
                        config=gen_config,
                    )
                    for chunk in response:
                        try:
                            if chunk.candidates and chunk.candidates[0].finish_reason is not None:
                                last_finish = chunk.candidates[0].finish_reason
                        except Exception:
                            pass
                        if chunk.text:
                            loop.call_soon_threadsafe(queue.put_nowait, chunk.text)
                    # Avisar si la generacion se trunco por limite de tokens.
                    # Critico con modelos thinking: los tokens de pensamiento consumen
                    # max_output_tokens y truncan la salida visible (incluidos [ACTION:...]).
                    if last_finish is not None and "MAX_TOKENS" in str(last_finish):
                        logger.warning(
                            f"[google] Generacion TRUNCADA por MAX_TOKENS "
                            f"(model={model}, max_output_tokens={max_tokens}). "
                            f"Si es un modelo de razonamiento, sube max_tokens — el pensamiento "
                            f"consume el presupuesto y corta la respuesta visible."
                        )
                except Exception as exc:
                    loop.call_soon_threadsafe(queue.put_nowait, exc)
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

            loop.run_in_executor(None, _sync_stream)

            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item

        except Exception as e:
            logger.error(f"[google] Error en streaming: {e}")
            error_msg = str(e)
            if "RESOURCE_EXHAUSTED" in error_msg and self._backend == "ai_studio":
                yield (
                    f"\n\n[Error del provider Google: 429 RESOURCE_EXHAUSTED. "
                    f"Los créditos de AI Studio se agotaron. "
                    f"Cambia el backend a 'Vertex AI' en Settings para usar tus créditos de Google Cloud ($300 Free Trial).]"
                )
            else:
                yield f"\n\n[Error del provider Google: {error_msg}]"

    async def generate_complete(
        self,
        messages: list[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        """Non-streaming generation."""
        if not self._client:
            self._configure()

        from google.genai import types

        system_instruction, contents = self._build_contents(messages)

        gen_config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system_instruction,
        )

        try:
            import asyncio as _asyncio
            _loop = _asyncio.get_running_loop()

            def _sync_complete():
                return self._client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=gen_config,
                )

            response = await _loop.run_in_executor(None, _sync_complete)

            text = response.text or ""
            input_tokens = 0
            output_tokens = 0

            try:
                if response.candidates and response.candidates[0].finish_reason is not None:
                    fr = response.candidates[0].finish_reason
                    if "MAX_TOKENS" in str(fr):
                        logger.warning(
                            f"[google] Generacion TRUNCADA por MAX_TOKENS "
                            f"(model={model}, max_output_tokens={max_tokens}). "
                            f"Si es un modelo de razonamiento, sube max_tokens."
                        )
            except Exception:
                pass

            if hasattr(response, "usage_metadata") and response.usage_metadata:
                input_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
                output_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

            return LLMResponse(
                text=text,
                model=model,
                provider="google",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        except Exception as e:
            logger.error(f"[google] Error en generate_complete: {e}")
            return LLMResponse(text=f"Error: {str(e)}", model=model, provider="google")

    async def list_models(self) -> list[str]:
        return config.get("providers", "google", "models", default=[])

    async def health_check(self) -> bool:
        try:
            if not self._client:
                self._configure()
            import asyncio as _asyncio
            _loop = _asyncio.get_running_loop()
            client = self._client

            def _sync_ping():
                client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents="ping",
                )

            await _loop.run_in_executor(None, _sync_ping)
            return True
        except Exception:
            return False
