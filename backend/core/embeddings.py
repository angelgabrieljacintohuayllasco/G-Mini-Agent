"""
G-Mini Agent — Embeddings para la memoria de largo plazo.

Proveedores reales (Vertex AI, Google AI Studio, OpenAI) con timeout y un
fallback léxico local (hash) que no sale de la máquina. Cada vector viaja con
el id del modelo que lo produjo: la memoria solo compara vectores del mismo
modelo y un fallo puntual de la red no mezcla espacios distintos.

Todo aquí es bloqueante (HTTP): llamarlo desde un hilo, nunca en el event loop.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from loguru import logger

from backend.config import config

HASH_MODEL_ID = "hash-v2"
HASH_DIM = 1024
DEFAULT_DIM = 768
LOCAL_PROVIDERS = {"ollama", "lmstudio", "local", "llamacpp"}

# Umbral mínimo de similitud para el recall, por familia de modelo. Los
# embeddings de Gemini concentran textos no relacionados en ~0,55-0,60
# (medido con frases en español), los de OpenAI quedan mucho más abajo.
_RECALL_FLOORS = (
    ("hash", 0.08),
    ("text-embedding-3", 0.25),
    ("gemini-embedding", 0.62),
    ("text-multilingual-embedding", 0.62),
    ("text-embedding-00", 0.62),
)

_STOPWORDS = frozenset(
    """a al algo como con cual de del el ella ellos en era es esa ese eso esta este esto ha hay la las le les lo los
    me mi mis muy no nos o para pero por que se si sin sobre su sus te tu un una uno y ya yo
    an and are as at be by for from has have i in is it its me my of on or our so that the this to was we were what
    with you your usuario usuaria user agente""".split()
)


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def lexical_tokens(text: str) -> list[str]:
    """Tokens normalizados (minúsculas, sin acentos ni stopwords)."""
    words = re.findall(r"\w+", _strip_accents((text or "").lower()))
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def recall_floor(model_id: str) -> float:
    configured = config.get("memory", "recall_min_similarity", default=None)
    if configured not in (None, ""):
        try:
            return float(configured)
        except (TypeError, ValueError):
            pass
    lowered = (model_id or "").lower()
    for prefix, floor in _RECALL_FLOORS:
        if prefix in lowered:
            return floor
    return 0.3


@dataclass
class Embedding:
    vector: list[float]
    model: str

    @property
    def dim(self) -> int:
        return len(self.vector)


class _Backend(Protocol):
    model_id: str

    def embed_many(self, texts: list[str], task: str) -> list[list[float]]: ...


class HashEmbedder:
    """Palabras + trigramas con hashing. Local, determinista y sin red.

    Los trigramas acercan formas de la misma palabra ("llamo" / "llama",
    "prefiero" / "prefiere"), algo frecuente en español.
    """

    model_id = HASH_MODEL_ID

    def __init__(self, dim: int = HASH_DIM) -> None:
        self.dim = dim

    def _add(self, vec: np.ndarray, feature: str, weight: float, hashes: int) -> None:
        digest = hashlib.md5(feature.encode("utf-8")).digest()
        for j in range(hashes):
            idx = int.from_bytes(digest[j * 4:(j + 1) * 4], "little") % self.dim
            vec[idx] += weight if digest[12 + j] & 1 else -weight

    def embed(self, text: str) -> list[float]:
        vec = np.zeros(self.dim, dtype=np.float32)
        for token in lexical_tokens(text):
            self._add(vec, "w:" + token, 1.0, 2)
            padded = f"^{token}$"
            for i in range(len(padded) - 2):
                self._add(vec, "t:" + padded[i:i + 3], 0.5, 1)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec.tolist()

    def embed_many(self, texts: list[str], task: str = "document") -> list[list[float]]:
        return [self.embed(t) for t in texts]


def _timeout_seconds() -> float:
    try:
        return max(2.0, float(config.get("memory", "embedding_timeout_s", default=10)))
    except (TypeError, ValueError):
        return 10.0


class _GenAIEmbedder:
    """Gemini embeddings vía google-genai (Vertex AI o AI Studio)."""

    _TASKS = {"document": "RETRIEVAL_DOCUMENT", "query": "RETRIEVAL_QUERY", "similarity": "SEMANTIC_SIMILARITY"}

    def __init__(self, *, vertex: bool, model: str, dim: int) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self.dim = dim
        http_options = types.HttpOptions(timeout=int(_timeout_seconds() * 1000))
        if vertex:
            from backend.providers import gcp_auth

            settings = gcp_auth.resolve_vertex_settings(config.get("providers", "vertex", default={}) or {})
            if not settings.project:
                raise ValueError("Vertex AI sin proyecto GCP (ADC o cuenta de servicio).")
            location = str(config.get("memory", "embedding_location", default="") or settings.location or "global")
            kwargs = {"vertexai": True, "project": settings.project, "location": location, "http_options": http_options}
            credentials = gcp_auth.load_credentials(settings.credentials_file)
            if credentials is not None:
                kwargs["credentials"] = credentials
            self._client = genai.Client(**kwargs)
        else:
            vault = config.get("providers", "google", "api_key_vault", default="google_api")
            api_key = config.get_api_key(vault)
            if not api_key:
                raise ValueError("Falta la API key de Google AI Studio.")
            self._client = genai.Client(api_key=api_key, http_options=http_options)
        self._model = model
        self.model_id = f"{model}@{dim}"

    def embed_many(self, texts: list[str], task: str) -> list[list[float]]:
        cfg = self._types.EmbedContentConfig(
            task_type=self._TASKS.get(task, "RETRIEVAL_DOCUMENT"),
            output_dimensionality=self.dim,
        )
        response = self._client.models.embed_content(
            model=self._model, contents=[t or " " for t in texts], config=cfg
        )
        vectors = [list(e.values) for e in (response.embeddings or [])]
        if len(vectors) != len(texts):
            raise RuntimeError(f"{self._model} devolvió {len(vectors)} vectores para {len(texts)} textos")
        return vectors


class _OpenAIEmbedder:
    def __init__(self, *, model: str) -> None:
        from openai import OpenAI

        api_key = config.get_api_key("openai_api")
        if not api_key:
            raise ValueError("Falta la API key de OpenAI.")
        self._client = OpenAI(api_key=api_key, timeout=_timeout_seconds(), max_retries=1)
        self._model = model
        self.model_id = model

    def embed_many(self, texts: list[str], task: str) -> list[list[float]]:
        response = self._client.embeddings.create(model=self._model, input=[t or " " for t in texts])
        return [list(item.embedding) for item in response.data]


def _vertex_available() -> bool:
    try:
        from backend.providers import gcp_auth

        settings = gcp_auth.resolve_vertex_settings(config.get("providers", "vertex", default={}) or {})
        return bool(settings.project)
    except Exception:
        return False


def _choose_backend(provider: str, model: str, dim: int) -> _Backend:
    """Elige el backend. `auto` prefiere la misma familia que el chat."""
    if provider == "hash":
        return HashEmbedder()
    chat_provider = str(config.get("model_router", "default_provider", default="") or "").lower()
    if provider == "auto":
        if chat_provider in LOCAL_PROVIDERS and not config.get("memory", "allow_cloud_when_local", default=False):
            return HashEmbedder()
        google_backend = str(config.get("providers", "google", "backend", default="ai_studio") or "")
        order = []
        if chat_provider == "openai":
            order.append("openai")
        if chat_provider == "vertex" or google_backend == "vertex_ai":
            order.append("vertex")
        order += ["google", "vertex", "openai"]
    else:
        order = [provider]

    errors = []
    for candidate in dict.fromkeys(order):
        try:
            if candidate == "vertex" and (provider != "auto" or _vertex_available()):
                return _GenAIEmbedder(vertex=True, model=model or "gemini-embedding-001", dim=dim)
            if candidate == "google" and (provider != "auto" or config.get_api_key("google_api")):
                return _GenAIEmbedder(vertex=False, model=model or "gemini-embedding-001", dim=dim)
            if candidate == "openai" and (provider != "auto" or config.get_api_key("openai_api")):
                return _OpenAIEmbedder(model=model or "text-embedding-3-small")
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    if errors:
        logger.warning(f"Embeddings remotos no disponibles ({'; '.join(errors)}); uso el modo local.")
    return HashEmbedder()


class EmbeddingProvider:
    """Fachada con circuit breaker: tras un fallo usa el modo local un rato."""

    def __init__(self, provider: str | None = None, model: str | None = None, dim: int | None = None) -> None:
        self._provider = (provider or config.get("memory", "embedding_provider", default="auto") or "auto").lower()
        self._model = model or str(config.get("memory", "embedding_model", default="") or "")
        self._dim = int(dim or config.get("memory", "embedding_dim", default=DEFAULT_DIM) or DEFAULT_DIM)
        self._backend: _Backend | None = None
        self._hash = HashEmbedder()
        self._down_until = 0.0
        self._lock = threading.Lock()

    @property
    def backend(self) -> _Backend:
        with self._lock:
            if self._backend is None:
                self._backend = _choose_backend(self._provider, self._model, self._dim)
                logger.info(f"Embeddings de memoria: {self._backend.model_id}")
            return self._backend

    @property
    def model_id(self) -> str:
        """Modelo activo (el que deberían tener todas las memorias)."""
        return self.backend.model_id

    def _cooldown_seconds(self) -> float:
        try:
            return float(config.get("memory", "embedding_cooldown_s", default=300))
        except (TypeError, ValueError):
            return 300.0

    def embed_many(self, texts: list[str], task: str = "document") -> list[Embedding]:
        backend = self.backend
        if isinstance(backend, HashEmbedder) or time.time() < self._down_until:
            return [Embedding(v, HASH_MODEL_ID) for v in self._hash.embed_many(texts)]
        try:
            vectors = backend.embed_many(texts, task)
            return [Embedding(v, backend.model_id) for v in vectors]
        except Exception as exc:
            cooldown = self._cooldown_seconds()
            if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                cooldown = min(cooldown, 60.0)  # cuota por minuto: vuelve pronto
            self._down_until = time.time() + cooldown
            logger.warning(
                f"Embeddings {backend.model_id} fallaron ({str(exc)[:200]}); modo local por {int(cooldown)} s."
            )
            return [Embedding(v, HASH_MODEL_ID) for v in self._hash.embed_many(texts)]

    def embed_text(self, text: str, task: str = "document") -> Embedding:
        return self.embed_many([text], task)[0]

    def embed(self, text: str) -> list[float]:
        return self.embed_text(text).vector


_embedder: EmbeddingProvider | None = None
_embedder_lock = threading.Lock()


def get_embedder() -> EmbeddingProvider:
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            _embedder = EmbeddingProvider()
        return _embedder


def reset_embedder() -> None:
    """Se llama al cambiar la config de memoria o las credenciales."""
    global _embedder
    with _embedder_lock:
        _embedder = None
