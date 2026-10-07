"""
G-Mini Agent — Clase base abstracta para proveedores LLM.

Todos los providers implementan esta interfaz. Regla de oro: ante un fallo del
proveedor (401, 404, 429, 5xx, timeout, conexión), el provider LANZA
`ProviderError`; nunca devuelve el error como si fuera texto del modelo. Así el
router puede hacer fallback/reintento y el agente no guarda el error en la
memoria ni lo cobra como uso.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

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
    thinking_tokens: int = 0
    finish_reason: str = ""


class ProviderError(RuntimeError):
    """
    Fallo de un proveedor LLM. `retriable=True` para errores transitorios
    (429, 5xx, timeout, conexión) que conviene reintentar o derivar a otro
    proveedor; `retriable=False` para errores de configuración (401, 404, 400).
    """

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        model: str = "",
        status: int | None = None,
        retriable: bool = False,
    ):
        self.provider = provider
        self.model = model
        self.status = status
        self.retriable = retriable
        super().__init__(f"[{provider}{':' + model if model else ''}] {message}")


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


# Códigos HTTP que conviene reintentar / derivar a otro proveedor.
RETRIABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def classify_http_status(status: int | None) -> bool:
    return status in RETRIABLE_STATUS if status is not None else False


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
        """Genera en streaming. Yields chunks de texto. Lanza ProviderError si falla."""
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
        """Genera una respuesta completa. Lanza ProviderError si falla."""
        ...

    @abstractmethod
    async def list_models(self) -> list[str]:
        """Lista los modelos disponibles en este provider."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Verifica si el provider está disponible."""
        ...

    def is_configured(self) -> bool:
        """True si el provider tiene lo necesario para funcionar (key o endpoint local)."""
        return True

    def last_usage(self) -> dict[str, Any] | None:
        """Último uso real reportado por el SDK (tokens), o None si no hay dato."""
        return getattr(self, "_last_usage", None)
