"""Proveedores por suscripción vía CLI (Claude Code, Codex) con CLIs falsos: mismo camino de subproceso."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from backend.providers import cli_provider
from backend.providers.base import LLMMessage, ProviderError
from backend.providers.cli_provider import CLIProvider, build_prompt

FAKE = r'''
import json, os, sys, time
log = os.environ["FAKE_CLI_LOG"]
mode = os.environ.get("FAKE_CLI_MODE", "ok")
prompt = sys.stdin.buffer.read().decode("utf-8")  # como node: UTF-8
env = {k: os.environ.get(k) for k in ("GEMINI_SYSTEM_MD", "GEMINI_CLI_HOME", "GOOGLE_GENAI_USE_VERTEXAI",
                                       "GOOGLE_CLOUD_PROJECT", "GEMINI_API_KEY")}
json.dump({"argv": sys.argv[1:], "stdin": prompt, "cwd": os.getcwd(), "env": env}, open(log, "w", encoding="utf-8"))
if mode == "slow":
    time.sleep(30)
if mode == "crash":
    sys.stderr.write("sesion vencida: ejecuta claude login\n")
    sys.exit(2)
flavor = os.environ["FAKE_CLI_FLAVOR"]
if flavor == "claude":
    for piece in ["Hola ", "Gabriel"]:
        print(json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                          "delta": {"type": "text_delta", "text": piece}}}), flush=True)
    print(json.dumps({"type": "result", "is_error": mode == "error", "result": "límite alcanzado",
                      "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 3}}), flush=True)
elif flavor == "gemini":
    print(json.dumps({"type": "init", "model": "gemini-3.8-flash"}), flush=True)
    for piece in ["Hola ", "Gabriel"]:
        print(json.dumps({"type": "message", "role": "assistant", "content": piece, "delta": True}), flush=True)
    status = "error" if mode == "error" else "success"
    print(json.dumps({"type": "result", "status": status, "message": "cuota agotada",
                      "stats": {"input_tokens": 30, "output_tokens": 2}}), flush=True)
else:
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Hola Gabriel"}}), flush=True)
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 4}}), flush=True)
'''


@pytest.fixture()
def fake_cli(tmp_path, monkeypatch):
    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE, encoding="utf-8")
    if os.name == "nt":
        wrapper = tmp_path / "fakecli.cmd"
        wrapper.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        wrapper = tmp_path / "fakecli"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    real_get = cli_provider.config.get
    overrides = {"binary": str(wrapper), "timeout_s": 15}

    def fake_get(*keys, default=None):
        if len(keys) == 3 and keys[0] == "providers" and keys[1] in cli_provider.CLI_FLAVORS:
            return overrides.get(keys[2], default)
        return real_get(*keys, default=default)

    monkeypatch.setattr(cli_provider.config, "get", fake_get)
    return {"log": log, "overrides": overrides}


MESSAGES = [
    LLMMessage(role="system", content="Eres G-Mini."),
    LLMMessage(role="user", content="Me llamo Gabriel"),
    LLMMessage(role="assistant", content="Hola"),
    LLMMessage(role="user", content="¿Cómo me llamo?"),
]


def test_prompt_carries_system_and_conversation():
    prompt = build_prompt(MESSAGES)
    assert prompt.index("<sistema>\nEres G-Mini.") < prompt.index("USUARIO: Me llamo Gabriel")
    assert "ASISTENTE: Hola" in prompt and prompt.rstrip().endswith("siguiendo <sistema>.")


async def test_claude_streams_and_isolates_the_users_setup(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_FLAVOR", "claude")
    monkeypatch.setenv("GMINI_SESSION_TOKEN", "secreto")
    provider = CLIProvider("claude-cli")
    chunks = [c async for c in provider.generate(MESSAGES, "haiku")]
    assert chunks == ["Hola ", "Gabriel"]
    assert provider.last_usage()["input_tokens"] == 15
    log = json.loads(fake_cli["log"].read_text(encoding="utf-8"))
    argv = log["argv"]
    assert argv[:1] == ["-p"] and argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == "" and "--strict-mcp-config" in argv
    assert argv[argv.index("--model") + 1] == "haiku" and "¿Cómo me llamo?" in log["stdin"]
    assert "gmini-cli-" in log["cwd"]  # carpeta temporal vacía, no la del usuario


async def test_codex_returns_the_final_message(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_FLAVOR", "codex")
    response = await CLIProvider("codex-cli").generate_complete(MESSAGES, "gpt-6-luna")
    assert response.text == "Hola Gabriel" and response.input_tokens == 20 and response.provider == "codex-cli"
    argv = json.loads(fake_cli["log"].read_text(encoding="utf-8"))["argv"]
    assert argv[:2] == ["exec", "--json"] and "--ignore-user-config" in argv and argv[-1] == "-"


async def test_gemini_uses_the_users_google_login(fake_cli, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_CLI_FLAVOR", "gemini")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini" / "oauth_creds.json").write_text("{}", encoding="utf-8")
    provider = CLIProvider("gemini-cli")
    response = await provider.generate_complete(MESSAGES, "gemini-3.8-flash")
    assert response.text == "Hola Gabriel" and response.input_tokens == 30
    log = json.loads(fake_cli["log"].read_text(encoding="utf-8"))
    argv = log["argv"]
    assert argv[argv.index("-o") + 1] == "stream-json" and argv[argv.index("--approval-mode") + 1] == "plan"
    assert argv[argv.index("-e") + 1] == "none" and argv[argv.index("-m") + 1] == "gemini-3.8-flash"
    assert log["env"]["GEMINI_SYSTEM_MD"].endswith("gmini-system.md") and log["env"]["GEMINI_CLI_HOME"] is None


async def test_gemini_with_vertex_gets_its_own_settings(fake_cli, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from backend import config as config_module
    from backend.providers import gcp_auth

    monkeypatch.setenv("FAKE_CLI_FLAVOR", "gemini")
    fake_cli["overrides"]["auth"] = "vertex"
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(gcp_auth, "resolve_vertex_settings", lambda section: SimpleNamespace(
        project="proyecto-1", location="global", credentials_file=""))
    await CLIProvider("gemini-cli").generate_complete(MESSAGES, "")
    env = json.loads(fake_cli["log"].read_text(encoding="utf-8"))["env"]
    assert env["GOOGLE_GENAI_USE_VERTEXAI"] == "true" and env["GOOGLE_CLOUD_PROJECT"] == "proyecto-1"
    settings = json.loads((tmp_path / "data" / "runtime" / "gemini-cli" / ".gemini" / "settings.json").read_text())
    assert settings["security"]["auth"]["selectedType"] == "vertex-ai"


async def test_gemini_error_result_raises(fake_cli, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_CLI_FLAVOR", "gemini")
    monkeypatch.setenv("FAKE_CLI_MODE", "error")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    fake_cli["overrides"]["auth"] = "login"
    with pytest.raises(ProviderError, match="cuota agotada"):
        [c async for c in CLIProvider("gemini-cli").generate(MESSAGES, "")]


@pytest.mark.parametrize("mode,match", [("crash", "sesion vencida"), ("error", "límite alcanzado")])
async def test_failures_raise_provider_errors(fake_cli, monkeypatch, mode, match):
    monkeypatch.setenv("FAKE_CLI_FLAVOR", "claude")
    monkeypatch.setenv("FAKE_CLI_MODE", mode)
    with pytest.raises(ProviderError, match=match):
        [c async for c in CLIProvider("claude-cli").generate(MESSAGES, "sonnet")]


async def test_timeout_kills_the_cli(fake_cli, monkeypatch):
    import time

    monkeypatch.setenv("FAKE_CLI_FLAVOR", "claude")
    monkeypatch.setenv("FAKE_CLI_MODE", "slow")
    fake_cli["overrides"]["timeout_s"] = 2
    started = time.monotonic()
    with pytest.raises(ProviderError) as exc:
        [c async for c in CLIProvider("claude-cli").generate(MESSAGES, "sonnet")]
    assert exc.value.retriable and time.monotonic() - started < 15


async def test_rejects_unsafe_model_names_and_missing_cli(fake_cli, monkeypatch):
    with pytest.raises(ProviderError, match="no válido"):
        [c async for c in CLIProvider("claude-cli").generate(MESSAGES, "haiku & del C:")]
    fake_cli["overrides"]["binary"] = "no-existe-este-cli"
    provider = CLIProvider("codex-cli")
    assert not provider.is_configured()
    with pytest.raises(ProviderError, match="No encontré"):
        await provider.generate_complete(MESSAGES, "")


def test_registry_and_router_know_the_cli_providers():
    from backend.providers import registry
    from backend.providers.router import _build_provider

    for pid in ("claude-cli", "codex-cli"):
        spec = registry.get_spec(pid)
        assert spec.kind == registry.KIND_CLI and not spec.to_dict()["requires_api_key"]
        assert isinstance(_build_provider(pid), CLIProvider)
