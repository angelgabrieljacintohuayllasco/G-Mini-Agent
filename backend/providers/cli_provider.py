"""
G-Mini Agent — Proveedores por suscripción vía los CLIs oficiales.

Usan tu plan de Claude (Claude Code) o de ChatGPT (Codex CLI) en lugar de una
API key: G-Mini ejecuta el CLI oficial en modo no interactivo y lee la
respuesta. Para que tu configuración personal del CLI no se mezcle con G-Mini
(CLAUDE.md, memoria, hooks, servidores MCP, AGENTS.md):

- Claude: sin herramientas (--tools ""), sin fuentes de configuración y sin
  persistir la sesión.
- Codex: sin config de usuario, sandbox de solo lectura, sesión efímera.
- Ambos corren en una carpeta temporal vacía y reciben el prompt por stdin
  (en Windows el shim .cmd corta la línea de comandos a 8191 caracteres).

Solo texto: las imágenes y archivos adjuntos no se envían por esta vía.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, AsyncIterator

from loguru import logger

from backend.config import config
from backend.providers.base import LLMMessage, LLMProvider, LLMResponse, ProviderError

DEFAULT_TIMEOUT_SECONDS = 300
_MODEL_RE = re.compile(r"^[\w.\-:/\[\]]{1,80}$")
# Va como argumento: sin < > & | ^ % ni comillas (en Windows pasa por cmd.exe).
_GUIDE = (
    "Eres el modelo de lenguaje detras de G-Mini Agent. El mensaje trae un bloque sistema con tus "
    "instrucciones y la conversacion hasta ahora. Sigue el bloque sistema y responde solo el siguiente "
    "turno del asistente, en su formato, incluidas las acciones ACTION si corresponden."
)

CLI_FLAVORS: dict[str, dict[str, Any]] = {
    "claude-cli": {"binary": "claude", "label": "Claude Code"},
    "codex-cli": {"binary": "codex", "label": "Codex CLI"},
}


def build_prompt(messages: list[LLMMessage]) -> str:
    """Sistema + conversación en un solo texto (los CLIs reciben un único prompt)."""
    system = "\n\n".join(m.content for m in messages if m.role == "system" and m.content)
    turns = []
    for message in messages:
        if message.role == "system":
            continue
        label = "USUARIO" if message.role == "user" else "ASISTENTE"
        text = message.content or ""
        if message.images or message.files:
            text += "\n[adjuntos no disponibles en este proveedor]"
        turns.append(f"{label}: {text}")
    parts = []
    if system:
        parts.append(f"<sistema>\n{system}\n</sistema>")
    parts.append("<conversacion>\n" + "\n\n".join(turns) + "\n</conversacion>")
    parts.append("Responde ahora como el ASISTENTE al último mensaje del USUARIO, siguiendo <sistema>.")
    return "\n\n".join(parts)


class CLIProvider(LLMProvider):
    def __init__(self, provider_id: str):
        if provider_id not in CLI_FLAVORS:
            raise ValueError(f"Proveedor CLI desconocido: {provider_id}")
        self.name = provider_id
        self._flavor = CLI_FLAVORS[provider_id]
        self._last_usage: dict[str, Any] | None = None

    # ── Estado ──────────────────────────────────────────────────────

    def _executable(self) -> str | None:
        custom = str(config.get("providers", self.name, "binary", default="") or "").strip()
        return shutil.which(custom or self._flavor["binary"])

    def is_configured(self) -> bool:
        return self._executable() is not None

    def last_usage(self) -> dict[str, Any] | None:
        return self._last_usage

    async def list_models(self) -> list[str]:
        from backend.providers import registry

        spec = registry.get_spec(self.name)
        return list(spec.default_models) if spec else []

    async def health_check(self) -> bool:
        return self.is_configured()

    def _timeout(self) -> float:
        try:
            return float(config.get("providers", self.name, "timeout_s", default=DEFAULT_TIMEOUT_SECONDS))
        except (TypeError, ValueError):
            return float(DEFAULT_TIMEOUT_SECONDS)

    # ── Línea de comandos ───────────────────────────────────────────

    def _command(self, executable: str, model: str) -> list[str]:
        if model and not _MODEL_RE.match(model):
            raise ProviderError(self.name, f"Nombre de modelo no válido: {model!r}", model=model, retriable=False)
        if self.name == "claude-cli":
            cmd = [executable, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                   "--tools", "", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config",
                   "--system-prompt", _GUIDE]
            if model and model != "default":
                cmd += ["--model", model]
            return cmd
        cmd = [executable, "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
               "-s", "read-only"]
        if model and model != "default":
            cmd += ["-m", model]
        return cmd + ["-"]

    # ── Ejecución ───────────────────────────────────────────────────

    async def _events(self, messages: list[LLMMessage], model: str) -> AsyncIterator[dict[str, Any]]:
        executable = self._executable()
        if executable is None:
            raise ProviderError(self.name, f"No encontré {self._flavor['label']} instalado.", model=model,
                                retriable=False)
        prompt = build_prompt(messages)
        workdir = tempfile.mkdtemp(prefix="gmini-cli-")
        env = {**os.environ, "NO_COLOR": "1"}
        env.pop("GMINI_SESSION_TOKEN", None)
        process = await asyncio.create_subprocess_exec(
            *self._command(executable, model), cwd=workdir, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        stderr_task = asyncio.create_task(process.stderr.read())
        try:
            process.stdin.write(prompt.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()
            deadline = asyncio.get_running_loop().time() + self._timeout()
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise ProviderError(self.name, f"{self._flavor['label']} no respondió a tiempo.", model=model,
                                        retriable=True)
                line = await asyncio.wait_for(process.stdout.readline(), remaining)
                if not line:
                    break
                try:
                    event = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event
            await asyncio.wait_for(process.wait(), 10)
            if process.returncode not in (0, None):
                detail = (await stderr_task).decode("utf-8", errors="replace").strip()[-400:]
                raise ProviderError(self.name, f"{self._flavor['label']} terminó con código {process.returncode}: "
                                    f"{detail or 'sin detalle'}", model=model, retriable=False)
        except asyncio.TimeoutError:
            raise ProviderError(self.name, f"{self._flavor['label']} no respondió a tiempo.", model=model,
                                retriable=True) from None
        finally:
            if process.returncode is None:
                _kill_tree(process.pid)
            if not stderr_task.done():
                stderr_task.cancel()
            shutil.rmtree(workdir, ignore_errors=True)

    async def generate(
        self,
        messages: list[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> AsyncIterator[str]:
        self._last_usage = None
        async for event in self._events(messages, model):
            if self.name == "claude-cli":
                text = _claude_delta(event)
                if text:
                    yield text
                if event.get("type") == "result":
                    if event.get("is_error"):
                        raise ProviderError(self.name, str(event.get("result") or "Error de Claude Code"),
                                            model=model, retriable=False)
                    self._last_usage = _claude_usage(event)
            else:
                text = _codex_text(event)
                if text:
                    yield text
                if event.get("type") in ("error", "turn.failed"):
                    detail = event.get("message") or (event.get("error") or {}).get("message") or "Error de Codex"
                    raise ProviderError(self.name, str(detail), model=model, retriable=False)
                if event.get("type") == "turn.completed":
                    self._last_usage = _codex_usage(event)

    async def generate_complete(
        self,
        messages: list[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        chunks = [chunk async for chunk in self.generate(messages, model, temperature, max_tokens)]
        usage = self._last_usage or {}
        return LLMResponse(
            text="".join(chunks), model=model or "default", provider=self.name,
            input_tokens=int(usage.get("input_tokens") or 0), output_tokens=int(usage.get("output_tokens") or 0),
            thinking_tokens=int(usage.get("thinking_tokens") or 0),
        )


def _claude_delta(event: dict[str, Any]) -> str:
    if event.get("type") != "stream_event":
        return ""
    inner = event.get("event") or {}
    delta = inner.get("delta") or {}
    if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
        return str(delta.get("text") or "")
    return ""


def _claude_usage(event: dict[str, Any]) -> dict[str, Any]:
    usage = event.get("usage") or {}
    return {
        "input_tokens": int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0)
        + int(usage.get("cache_creation_input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "thinking_tokens": int((usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0),
    }


def _codex_text(event: dict[str, Any]) -> str:
    item = event.get("item") or {}
    if event.get("type") == "item.completed" and item.get("type") == "agent_message":
        return str(item.get("text") or "")
    return ""


def _codex_usage(event: dict[str, Any]) -> dict[str, Any]:
    usage = event.get("usage") or {}
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "thinking_tokens": int(usage.get("reasoning_output_tokens") or 0),
    }


def _kill_tree(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.kill(pid, 9)
    except Exception as exc:
        logger.debug(f"cli provider: no se pudo cerrar {pid}: {exc}")
