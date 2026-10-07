"""Providers y router: errores tipados, parámetros por modelo y fallback real (sin red)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from backend.providers import registry
from backend.providers.base import LLMMessage, LLMProvider, LLMProviderUnavailableError, LLMResponse, ProviderError

JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 64).decode()


# ── Parámetros por modelo ──────────────────────────────────────────────

def test_openai_reasoning_models_use_max_completion_tokens_without_temperature():
    params = registry.openai_chat_params("openai", "gpt-6.1-sol", temperature=0.2, max_tokens=900, stream=True)
    assert params == {"max_completion_tokens": 900}


def test_regular_openai_compat_models_keep_temperature():
    params = registry.openai_chat_params("groq", "llama-3.3-70b-versatile", temperature=0.2, max_tokens=900, stream=True)
    assert params == {"max_tokens": 900, "temperature": 0.2}


def test_kimi_fixed_temperature_and_dashscope_thinking_off():
    assert registry.openai_chat_params("moonshot", "kimi-k3", temperature=0.2, max_tokens=10, stream=True)["temperature"] == 1.0
    params = registry.openai_chat_params("dashscope", "qwen3-max", temperature=0.5, max_tokens=10, stream=False)
    assert params["extra_body"] == {"enable_thinking": False}


@pytest.mark.parametrize("model,uses", [
    ("claude-opus-5-5", False), ("claude-sonnet-5-5", False), ("claude-fable-5-1", False),
    ("claude-opus-4-8", False), ("claude-haiku-4-5", True), ("claude-sonnet-4-6", True),
])
def test_anthropic_sampling_rules(model, uses):
    assert registry.anthropic_uses_sampling(model) is uses


# ── OpenAI-compatible ──────────────────────────────────────────────────

def _openai_provider(handler, name="openai"):
    from openai import AsyncOpenAI

    from backend.providers.openai_compat import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(name)
    provider._client = AsyncOpenAI(
        api_key="k", base_url="https://mock.test/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return provider


async def test_openai_compat_errors_are_typed():
    def handler(request):
        return httpx.Response(503, json={"error": {"message": "overloaded"}})

    provider = _openai_provider(handler)
    with pytest.raises(ProviderError) as exc:
        await provider.generate_complete([LLMMessage(role="user", content="hola")], "gpt-6.1-sol")
    assert exc.value.retriable is True and exc.value.status == 503

    def handler401(request):
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    provider = _openai_provider(handler401)
    with pytest.raises(ProviderError) as exc:
        await provider.generate_complete([LLMMessage(role="user", content="hola")], "gpt-6.1-sol")
    assert exc.value.retriable is False


async def test_openai_compat_request_body_for_reasoning_model_and_jpeg():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "gpt-6.1-sol",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "listo"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
        })

    provider = _openai_provider(handler)
    resp = await provider.generate_complete(
        [LLMMessage(role="user", content="mira", images=[JPEG_B64])], "gpt-6.1-sol", temperature=0.3, max_tokens=50,
    )
    assert resp.text == "listo" and resp.input_tokens == 7
    assert seen["max_completion_tokens"] == 50
    assert "temperature" not in seen and "max_tokens" not in seen
    image_part = seen["messages"][0]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


# ── Anthropic ──────────────────────────────────────────────────────────

def _anthropic_provider(handler):
    from anthropic import AsyncAnthropic

    from backend.providers.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider()
    provider._client = AsyncAnthropic(
        api_key="k", base_url="https://mock.test", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return provider


def _anthropic_ok(seen):
    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": seen.get("model", "x"),
            "content": [{"type": "text", "text": "hola"}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 5, "output_tokens": 1},
        })
    return handler


async def test_anthropic_new_models_omit_temperature_and_use_adaptive_thinking():
    seen: dict = {}
    provider = _anthropic_provider(_anthropic_ok(seen))
    await provider.generate_complete([LLMMessage(role="user", content="hola")], "claude-opus-5-5", temperature=0.4)
    assert "temperature" not in seen
    assert seen["thinking"] == {"type": "adaptive"}


async def test_anthropic_haiku_keeps_temperature_and_labels_jpeg():
    seen: dict = {}
    provider = _anthropic_provider(_anthropic_ok(seen))
    await provider.generate_complete(
        [LLMMessage(role="user", content="", images=[JPEG_B64])], "claude-haiku-4-5", temperature=0.4,
    )
    assert seen["temperature"] == 0.4
    blocks = seen["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/jpeg"
    assert all(not (b["type"] == "text" and b["text"] == "") for b in blocks)


async def test_anthropic_errors_are_typed():
    def handler(request):
        return httpx.Response(429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}})

    provider = _anthropic_provider(handler)
    with pytest.raises(ProviderError) as exc:
        await provider.generate_complete([LLMMessage(role="user", content="hola")], "claude-sonnet-5-5")
    assert exc.value.retriable is True


# ── Google: varios mensajes de sistema se concatenan ──────────────────

def test_google_concatenates_system_messages():
    from backend.providers.google_provider import GoogleProvider

    provider = GoogleProvider.__new__(GoogleProvider)
    system, contents = provider._build_contents([
        LLMMessage(role="system", content="Regla base"),
        LLMMessage(role="system", content="[Contexto comprimido]"),
        LLMMessage(role="user", content="hola"),
    ])
    assert "Regla base" in system and "[Contexto comprimido]" in system
    assert len(contents) == 1


# ── Cohere SDK 7.x ─────────────────────────────────────────────────────

def test_cohere_usage_and_delta_parsing():
    from types import SimpleNamespace as NS

    from backend.providers.cohere_provider import _delta_text, _usage_tokens

    assert _usage_tokens(NS(tokens=NS(input_tokens=3, output_tokens=4))) == (3, 4)
    assert _usage_tokens(NS(tokens={"input_tokens": 1, "output_tokens": 2})) == (1, 2)
    assert _delta_text(NS(delta=NS(message=NS(content=NS(text="hola"))))) == "hola"


# ── Router: fallback real ──────────────────────────────────────────────

class _FakeProvider(LLMProvider):
    def __init__(self, name, *, chunks=None, fail_before=None, fail_after_first=None, configured=True):
        self.name = name
        self.chunks = chunks or ["ok"]
        self.fail_before = fail_before
        self.fail_after_first = fail_after_first
        self.configured = configured
        self.calls = 0

    async def generate(self, messages, model, temperature=0.7, max_tokens=4096, stream=True, **kw):
        self.calls += 1
        if self.fail_before:
            raise self.fail_before
        for i, chunk in enumerate(self.chunks):
            if i == 1 and self.fail_after_first:
                raise self.fail_after_first
            yield chunk

    async def generate_complete(self, messages, model, temperature=0.7, max_tokens=4096, **kw):
        self.calls += 1
        if self.fail_before:
            raise self.fail_before
        return LLMResponse(text="".join(self.chunks), model=model, provider=self.name)

    async def list_models(self):
        return []

    async def health_check(self):
        return True

    def is_configured(self):
        return self.configured


@pytest.fixture()
def router(monkeypatch):
    from backend.config import config
    from backend.providers.router import ModelRouter

    r = ModelRouter.__new__(ModelRouter)
    r._providers = {}
    r._last_generation_meta = {}
    r._last_optimization = None
    r._models_catalog = {}
    monkeypatch.setitem(config.data, "model_router", {
        **(config.get("model_router") or {}),
        "fallback_order": ["nokey:m2", "backup:m3"],
    })
    return r


async def _collect(gen):
    return [c async for c in gen]


async def test_router_falls_back_when_primary_fails_before_first_chunk(router):
    primary = _FakeProvider("main", fail_before=ProviderError("main", "503", retriable=True))
    router._providers = {
        "main": primary,
        "nokey": _FakeProvider("nokey", configured=False),
        "backup": _FakeProvider("backup", chunks=["hola", " mundo"]),
    }
    chunks = await _collect(router.generate([LLMMessage(role="user", content="x")], model="m1", provider_name="main"))
    assert chunks == ["hola", " mundo"]
    assert router._providers["nokey"].calls == 0          # sin credenciales: se salta
    meta = router.get_last_generation_meta()
    assert meta["provider"] == "backup" and meta["fallback"] is True


async def test_router_does_not_duplicate_output_when_stream_breaks_midway(router):
    router._providers = {
        "main": _FakeProvider("main", chunks=["uno", "dos"], fail_after_first=ProviderError("main", "reset", retriable=True)),
        "backup": _FakeProvider("backup", chunks=["otro"]),
    }
    received = []
    with pytest.raises(ProviderError):
        async for chunk in router.generate([LLMMessage(role="user", content="x")], model="m1", provider_name="main"):
            received.append(chunk)
    assert received == ["uno"]
    assert router._providers["backup"].calls == 0


async def test_router_raises_unavailable_with_last_error(router):
    router._providers = {"main": _FakeProvider("main", fail_before=ProviderError("main", "401 bad key"))}
    with pytest.raises(LLMProviderUnavailableError) as exc:
        await router.generate_complete([LLMMessage(role="user", content="x")], model="m1", provider_name="main")
    assert "401" in str(exc.value)


async def test_router_never_guesses_local_model(router):
    class _Down(_FakeProvider):
        async def list_models(self):
            raise ConnectionError("refused")

    router._providers = {"ollama": _Down("ollama")}
    assert await router._get_local_model("ollama") is None
