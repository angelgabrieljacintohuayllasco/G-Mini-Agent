"""
G-Mini Agent — Registro central de proveedores LLM.

Fuente única de metadatos de cada proveedor (tipo de adaptador, base_url,
nombre de la credencial en el vault, enlaces de ayuda y modelos sugeridos).
El router, la API REST y la UI de ajustes leen de aquí, así que agregar un
proveedor compatible con OpenAI es agregar una entrada en PROVIDERS.

config.yaml puede sobreescribir base_url, api_key_vault y models por proveedor
(sección providers.<id>), sin tocar este archivo.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Tipos de adaptador soportados por el router.
KIND_OPENAI_COMPAT = "openai_compat"
KIND_ANTHROPIC = "anthropic"
KIND_GOOGLE = "google"
KIND_VERTEX = "vertex"
KIND_COHERE = "cohere"


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    kind: str
    base_url: str = ""
    api_key_vault: str = ""          # vacío = no requiere API key (locales)
    local: bool = False
    category: str = "cloud"          # cloud | aggregator | local | enterprise
    docs_url: str = ""
    key_url: str = ""                # dónde se obtiene la API key
    supports_model_listing: bool = True   # GET {base_url}/models disponible
    default_models: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""
    # OpenAI-compat: modelos (prefijos) que exigen max_completion_tokens y no aceptan temperature.
    reasoning_model_prefixes: tuple[str, ...] = field(default_factory=tuple)
    # True si el usuario debe escribir la base_url (Azure, servidores propios).
    requires_base_url: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["default_models"] = list(self.default_models)
        data["reasoning_model_prefixes"] = list(self.reasoning_model_prefixes)
        data["requires_api_key"] = bool(self.api_key_vault) and not self.local
        return data


_OPENAI_REASONING = ("gpt-5", "gpt-6", "o1", "o3", "o4")

PROVIDERS: dict[str, ProviderSpec] = {
    # ── Nativos ──────────────────────────────────────────────────────────
    "openai": ProviderSpec(
        id="openai", label="OpenAI", kind=KIND_OPENAI_COMPAT,
        base_url="https://api.openai.com/v1", api_key_vault="openai_api",
        docs_url="https://developers.openai.com/api/docs/models",
        key_url="https://platform.openai.com/api-keys",
        default_models=("gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna"),
        reasoning_model_prefixes=_OPENAI_REASONING,
        description="GPT-6 Astra, GPT-6.1 Sol y GPT-6 Luna.",
    ),
    "anthropic": ProviderSpec(
        id="anthropic", label="Anthropic (Claude)", kind=KIND_ANTHROPIC,
        base_url="https://api.anthropic.com", api_key_vault="anthropic_api",
        docs_url="https://platform.claude.com/docs/en/models/overview",
        key_url="https://platform.claude.com/settings/keys",
        default_models=("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-haiku-4-5"),
        description="Claude Opus 5.5, Sonnet 5.5, Fable 5.1 y Haiku 4.5.",
    ),
    "google": ProviderSpec(
        id="google", label="Google AI Studio (Gemini)", kind=KIND_GOOGLE,
        base_url="https://generativelanguage.googleapis.com/v1beta", api_key_vault="google_api",
        docs_url="https://ai.google.dev/gemini-api/docs/models",
        key_url="https://aistudio.google.com/apikey",
        supports_model_listing=False,
        default_models=("gemini-3.8-flash", "gemini-3.1-pro-preview", "gemini-3.5-flash-lite"),
        description="Gemini con API key de AI Studio.",
    ),
    "vertex": ProviderSpec(
        id="vertex", label="Google Vertex AI", kind=KIND_VERTEX,
        category="enterprise",
        docs_url="https://cloud.google.com/vertex-ai/generative-ai/docs/models",
        key_url="https://cloud.google.com/docs/authentication/provide-credentials-adc",
        supports_model_listing=False,
        default_models=("gemini-3.8-flash", "gemini-3.1-pro-preview", "gemini-3.5-flash-lite"),
        description=(
            "Gemini en Google Cloud con tus créditos de GCP. Usa Application Default "
            "Credentials (gcloud auth application-default login) o una cuenta de servicio."
        ),
    ),
    "cohere": ProviderSpec(
        id="cohere", label="Cohere", kind=KIND_COHERE,
        base_url="https://api.cohere.ai/v1", api_key_vault="cohere_api",
        docs_url="https://docs.cohere.com/docs/models",
        key_url="https://dashboard.cohere.com/api-keys",
        supports_model_listing=False,
        default_models=("command-a-03-2025", "command-r-plus", "command-r"),
    ),
    # ── Compatibles con OpenAI (cloud) ───────────────────────────────────
    "xai": ProviderSpec(
        id="xai", label="xAI (Grok)", kind=KIND_OPENAI_COMPAT,
        base_url="https://api.x.ai/v1", api_key_vault="xai_api",
        docs_url="https://docs.x.ai/docs/models", key_url="https://console.x.ai",
        default_models=("grok-4.7", "grok-4.6", "grok-4.3"),
    ),
    "deepseek": ProviderSpec(
        id="deepseek", label="DeepSeek", kind=KIND_OPENAI_COMPAT,
        base_url="https://api.deepseek.com", api_key_vault="deepseek_api",
        docs_url="https://api-docs.deepseek.com/quick_start/pricing",
        key_url="https://platform.deepseek.com/api_keys",
        default_models=("deepseek-flash", "deepseek-v4-pro"),
    ),
    "groq": ProviderSpec(
        id="groq", label="Groq", kind=KIND_OPENAI_COMPAT,
        base_url="https://api.groq.com/openai/v1", api_key_vault="groq_api",
        docs_url="https://console.groq.com/docs/models", key_url="https://console.groq.com/keys",
        default_models=("openai/gpt-oss-120b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
    ),
    "mistral": ProviderSpec(
        id="mistral", label="Mistral AI", kind=KIND_OPENAI_COMPAT,
        base_url="https://api.mistral.ai/v1", api_key_vault="mistral_api",
        docs_url="https://docs.mistral.ai/getting-started/models/models_overview/",
        key_url="https://console.mistral.ai/api-keys",
        default_models=("mistral-large-latest", "mistral-medium-latest", "mistral-small-latest", "codestral-25-08"),
    ),
    "perplexity": ProviderSpec(
        id="perplexity", label="Perplexity", kind=KIND_OPENAI_COMPAT,
        base_url="https://api.perplexity.ai", api_key_vault="perplexity_api",
        docs_url="https://docs.perplexity.ai/getting-started/models",
        key_url="https://www.perplexity.ai/settings/api",
        supports_model_listing=False,
        default_models=("sonar-pro", "sonar", "sonar-reasoning-pro"),
        description="Respuestas con búsqueda web incluida.",
    ),
    "moonshot": ProviderSpec(
        id="moonshot", label="Moonshot (Kimi)", kind=KIND_OPENAI_COMPAT,
        base_url="https://api.moonshot.ai/v1", api_key_vault="moonshot_api",
        docs_url="https://platform.kimi.ai/docs/guide/kimi-k3-quickstart",
        key_url="https://platform.kimi.ai/console/api-keys",
        default_models=("kimi-k3",),
    ),
    "dashscope": ProviderSpec(
        id="dashscope", label="Alibaba Qwen (DashScope)", kind=KIND_OPENAI_COMPAT,
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1", api_key_vault="dashscope_api",
        docs_url="https://www.alibabacloud.com/help/en/model-studio/models",
        key_url="https://modelstudio.console.alibabacloud.com/?tab=playground#/api-key",
        default_models=("qwen-max", "qwen-plus", "qwen-flash"),
    ),
    "zai": ProviderSpec(
        id="zai", label="Z.ai (GLM)", kind=KIND_OPENAI_COMPAT,
        base_url="https://api.z.ai/api/paas/v4", api_key_vault="zai_api",
        docs_url="https://docs.z.ai/guides/overview/pricing", key_url="https://z.ai/manage-apikey/apikey-list",
        default_models=("glm-5.3", "glm-5.3-flash"),
    ),
    "minimax": ProviderSpec(
        id="minimax", label="MiniMax", kind=KIND_OPENAI_COMPAT,
        base_url="https://api.minimax.io/v1", api_key_vault="minimax_api",
        docs_url="https://platform.minimax.io/docs/guides/models-intro",
        key_url="https://platform.minimax.io/user-center/basic-information/interface-key",
    ),
    "cerebras": ProviderSpec(
        id="cerebras", label="Cerebras", kind=KIND_OPENAI_COMPAT,
        base_url="https://api.cerebras.ai/v1", api_key_vault="cerebras_api",
        docs_url="https://inference-docs.cerebras.ai/models/overview", key_url="https://cloud.cerebras.ai",
        description="Inferencia ultrarrápida.",
    ),
    "sambanova": ProviderSpec(
        id="sambanova", label="SambaNova", kind=KIND_OPENAI_COMPAT,
        base_url="https://api.sambanova.ai/v1", api_key_vault="sambanova_api",
        docs_url="https://docs.sambanova.ai/cloud/docs/get-started/supported-models",
        key_url="https://cloud.sambanova.ai/apis",
    ),
    "venice": ProviderSpec(
        id="venice", label="Venice AI", kind=KIND_OPENAI_COMPAT,
        base_url="https://api.venice.ai/api/v1", api_key_vault="venice_api",
        docs_url="https://docs.venice.ai/models/overview", key_url="https://venice.ai/settings/api",
        description="Inferencia privada sin retención de datos.",
    ),
    # ── Agregadores ──────────────────────────────────────────────────────
    "openrouter": ProviderSpec(
        id="openrouter", label="OpenRouter", kind=KIND_OPENAI_COMPAT, category="aggregator",
        base_url="https://openrouter.ai/api/v1", api_key_vault="openrouter_api",
        docs_url="https://openrouter.ai/models", key_url="https://openrouter.ai/settings/keys",
        default_models=("openrouter/auto", "anthropic/claude-sonnet-5.5", "google/gemini-3.8-flash", "openai/gpt-6.1-sol"),
        description="Cientos de modelos con una sola key.",
    ),
    "together": ProviderSpec(
        id="together", label="Together AI", kind=KIND_OPENAI_COMPAT, category="aggregator",
        base_url="https://api.together.xyz/v1", api_key_vault="together_api",
        docs_url="https://docs.together.ai/docs/serverless-models",
        key_url="https://api.together.ai/settings/api-keys",
    ),
    "fireworks": ProviderSpec(
        id="fireworks", label="Fireworks AI", kind=KIND_OPENAI_COMPAT, category="aggregator",
        base_url="https://api.fireworks.ai/inference/v1", api_key_vault="fireworks_api",
        docs_url="https://fireworks.ai/models", key_url="https://fireworks.ai/account/api-keys",
    ),
    "deepinfra": ProviderSpec(
        id="deepinfra", label="DeepInfra", kind=KIND_OPENAI_COMPAT, category="aggregator",
        base_url="https://api.deepinfra.com/v1/openai", api_key_vault="deepinfra_api",
        docs_url="https://deepinfra.com/models", key_url="https://deepinfra.com/dash/api_keys",
    ),
    "nvidia": ProviderSpec(
        id="nvidia", label="NVIDIA NIM", kind=KIND_OPENAI_COMPAT, category="aggregator",
        base_url="https://integrate.api.nvidia.com/v1", api_key_vault="nvidia_api",
        docs_url="https://build.nvidia.com/models", key_url="https://build.nvidia.com/settings/api-keys",
    ),
    "huggingface": ProviderSpec(
        id="huggingface", label="Hugging Face", kind=KIND_OPENAI_COMPAT, category="aggregator",
        base_url="https://router.huggingface.co/v1", api_key_vault="huggingface_api",
        docs_url="https://huggingface.co/docs/inference-providers/index",
        key_url="https://huggingface.co/settings/tokens",
        default_models=("openai/gpt-oss-120b",),
    ),
    "github": ProviderSpec(
        id="github", label="GitHub Models", kind=KIND_OPENAI_COMPAT, category="aggregator",
        base_url="https://models.github.ai/inference", api_key_vault="github_models_api",
        docs_url="https://docs.github.com/en/github-models",
        key_url="https://github.com/settings/personal-access-tokens",
        supports_model_listing=False,
        default_models=("openai/gpt-4.1", "openai/gpt-4.1-mini"),
        description="Gratis con tu cuenta de GitHub (token con permiso models:read).",
    ),
    # ── Empresariales ────────────────────────────────────────────────────
    "azure_openai": ProviderSpec(
        id="azure_openai", label="Azure OpenAI", kind=KIND_OPENAI_COMPAT, category="enterprise",
        base_url="", api_key_vault="azure_openai_api",
        docs_url="https://learn.microsoft.com/azure/ai-foundry/openai/latest",
        key_url="https://ai.azure.com",
        supports_model_listing=False,
        requires_base_url=True,
        reasoning_model_prefixes=_OPENAI_REASONING,
        description="Usa https://<recurso>.openai.azure.com/openai/v1/ y el nombre del deployment como modelo.",
    ),
    # ── Locales ──────────────────────────────────────────────────────────
    "ollama": ProviderSpec(
        id="ollama", label="Ollama", kind=KIND_OPENAI_COMPAT, category="local", local=True,
        base_url="http://localhost:11434/v1",
        docs_url="https://ollama.com/library", key_url="https://ollama.com/download",
    ),
    "lmstudio": ProviderSpec(
        id="lmstudio", label="LM Studio", kind=KIND_OPENAI_COMPAT, category="local", local=True,
        base_url="http://localhost:1234/v1",
        docs_url="https://lmstudio.ai/docs/app/api", key_url="https://lmstudio.ai",
    ),
    "custom": ProviderSpec(
        id="custom", label="Servidor propio (OpenAI-compatible)", kind=KIND_OPENAI_COMPAT,
        category="local", api_key_vault="custom_api",
        base_url="http://localhost:8000/v1", requires_base_url=True,
        docs_url="https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html",
        description="vLLM, llama.cpp server, LocalAI, Jan, LiteLLM u otro endpoint compatible.",
    ),
}

CATEGORY_LABELS = {
    "cloud": "Nube",
    "aggregator": "Agregadores",
    "enterprise": "Empresarial",
    "local": "Local",
}


def get_spec(provider_id: str) -> ProviderSpec | None:
    return PROVIDERS.get(str(provider_id or "").strip())


def provider_ids(kind: str | None = None) -> list[str]:
    return [pid for pid, spec in PROVIDERS.items() if kind is None or spec.kind == kind]


def openai_compat_ids() -> list[str]:
    return provider_ids(KIND_OPENAI_COMPAT)


def resolve_provider_settings(provider_id: str, overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Combina el spec con la sección providers.<id> de config (config gana)."""
    spec = get_spec(provider_id)
    overrides = overrides if isinstance(overrides, dict) else {}
    base = {
        "base_url": spec.base_url if spec else "",
        "api_key_vault": spec.api_key_vault if spec else "",
        "models": list(spec.default_models) if spec else [],
    }
    for key in ("base_url", "api_key_vault"):
        value = overrides.get(key)
        if isinstance(value, str) and value.strip():
            base[key] = value.strip()
    models = overrides.get("models")
    if isinstance(models, list) and models:
        base["models"] = [str(m) for m in models if str(m).strip()]
    return base


def is_reasoning_model(provider_id: str, model: str) -> bool:
    """True si el modelo es de razonamiento estilo OpenAI (max_completion_tokens, sin temperature)."""
    spec = get_spec(provider_id)
    if not spec or not spec.reasoning_model_prefixes:
        return False
    name = str(model or "").strip().lower()
    return any(name.startswith(prefix) for prefix in spec.reasoning_model_prefixes)


def list_specs() -> list[dict[str, Any]]:
    return [spec.to_dict() for spec in PROVIDERS.values()]


# Modelos Claude que YA NO aceptan temperature/top_p (usan thinking adaptativo).
# Opus 4.7+, Opus 5/5.5, Sonnet 5/5.5, Fable/Mythos 5.x. Haiku 4.5, los 4.6 y
# anteriores sí aceptan sampling.
_ANTHROPIC_NO_SAMPLING_PREFIXES = (
    "claude-opus-4-7", "claude-opus-4-8", "claude-opus-5",
    "claude-sonnet-5", "claude-fable-5", "claude-mythos-5",
)


def anthropic_uses_sampling(model: str) -> bool:
    name = str(model or "").strip().lower()
    return not any(name.startswith(prefix) for prefix in _ANTHROPIC_NO_SAMPLING_PREFIXES)


# Modelos (por prefijo) que solo aceptan temperature=1 fijo.
_FIXED_TEMPERATURE_PREFIXES = {
    "moonshot": ("kimi-k2.6", "kimi-k2-thinking", "kimi-k3"),
}


def openai_chat_params(
    provider_id: str,
    model: str,
    *,
    temperature: float,
    max_tokens: int,
    stream: bool,
) -> dict[str, Any]:
    """
    Construye los kwargs de chat.completions según las rarezas del proveedor/modelo:
    - Modelos de razonamiento (gpt-5/6, o-series): max_completion_tokens y sin temperature.
    - Kimi K2.6+: temperature fija en 1.
    - DashScope Qwen3 sin streaming: extra_body={"enable_thinking": False}.
    """
    name = str(model or "").strip().lower()
    params: dict[str, Any] = {}

    if is_reasoning_model(provider_id, model):
        params["max_completion_tokens"] = max_tokens
    else:
        params["max_tokens"] = max_tokens
        fixed = _FIXED_TEMPERATURE_PREFIXES.get(provider_id, ())
        if any(name.startswith(p) for p in fixed):
            params["temperature"] = 1.0
        else:
            params["temperature"] = temperature

    if provider_id == "dashscope" and name.startswith("qwen3") and not stream:
        params["extra_body"] = {"enable_thinking": False}

    return params

