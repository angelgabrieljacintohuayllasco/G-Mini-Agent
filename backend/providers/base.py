"""
G-Mini Agent — Clase base abstracta para proveedores LLM.
Todos los providers implementan esta interfaz.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator

from pydantic import BaseModel


class LLMMessage(BaseModel):
    role: str          # system | user | assistant
    content: str
    images: list[str] = []  # base64 images (para multimodal)
    files: list[dict] = []  # adjuntos no-imagen: [{"data": base64, "mime_type": str, "file_name": str}]


class LLMResponse(BaseModel):
    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""


class LLMProviderUnavailableError(RuntimeError):
    """Todos los proveedores LLM fallaron tras agotar la cadena de fallback."""

    def __init__(self, providers_tried: list[str], last_error: str = ""):
        self.providers_tried = providers_tried
        self.last_error = last_error
        tried = ", ".join(providers_tried) if providers_tried else "ninguno"
        msg = f"Ningun proveedor disponible (intentados: {tried})"
        if last_error:
            msg += f". Ultimo error: {last_error}"
        super().__init__(msg)


class LLMProvider(ABC):
    """Interfaz abstracta que todos los providers deben implementar."""

    name: str = "base"

    @abstractmethod
    async def generate(
        self,
        messages: list[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = True,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """
        Genera una respuesta del LLM en modo streaming.
        Yields chunks de texto conforme llegan.
        """
        ...

    @abstractmethod
    async def generate_complete(
        self,
        messages: list[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        """
        Genera una respuesta completa (sin streaming).
        """
        ...

    @abstractmethod
    async def list_models(self) -> list[str]:
        """Lista los modelos disponibles en este provider."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Verifica si el provider está disponible."""
        ...
