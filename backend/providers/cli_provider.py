"""
G-Mini Agent — Proveedores por suscripción vía los CLIs oficiales.

Usan tu plan de Claude (Claude Code), de ChatGPT (Codex CLI) o tu cuenta de
Google (Gemini CLI) en lugar de una API key: G-Mini ejecuta el CLI oficial en
modo no interactivo y lee la respuesta. Para que tu configuración personal del
CLI no se mezcle con G-Mini (CLAUDE.md, memoria, hooks, servidores MCP,
AGENTS.md, extensiones):

- Claude: sin herramientas (--tools ""), sin fuentes de configuración y sin
  persistir la sesión.
- Codex: sin config de usuario, sandbox de solo lectura, sesión efímera.
- Gemini: modo plan (solo lectura), sin extensiones y con un prompt de sistema
  corto (GEMINI_SYSTEM_MD). Con auth "login" usa tu sesión de Google; con
  "vertex" o "api_key", una carpeta de configuración propia de G-Mini.
- Todos corren en una carpeta temporal vacía y reciben el prompt por stdin
  (en Windows el shim .cmd corta la línea de comandos a 8191 caracteres).

Solo texto: las imágenes y archivos adjuntos no se envían por esta vía.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import signal
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
    "gemini-cli": {"binary": "gemini", "label": "Gemini CLI"},
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
        if self.name == "gemini-cli":
            # -p se agrega al texto que llega por stdin; el prompt completo va por stdin.
            cmd = [executable, "-p", "Responde siguiendo el bloque sistema.", "-o", "stream-json",
                   "--approval-mode", "plan", "--skip-trust", "-e", "none"]
            if model and model != "default":
                cmd += ["-m", model]
            return cmd
        cmd = [executable, "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
               "-s", "read-only"]
        if model and model != "default":
            cmd += ["-m", model]
        return cmd + ["-"]

    # ── Gemini CLI: autenticación ───────────────────────────────────

    def _gemini_env(self, workdir: str) -> dict[str, str]:
        """Variables para Gemini CLI según providers.gemini-cli.auth (login | vertex | api_key | auto)."""
        guide = os.path.join(workdir, "gmini-system.md")
        with open(guide, "w", encoding="utf-8") as handle:
            handle.write(_GUIDE + "\n")
        env = {"GEMINI_SYSTEM_MD": guide}

        mode = str(config.get("providers", self.name, "auth", default="auto") or "auto").strip().lower()
        user_login = os.path.isfile(os.path.join(os.path.expanduser("~"), ".gemini", "oauth_creds.json"))
        api_key = config.get_api_key("google_api") if mode in ("auto", "api_key") else None
        if mode == "auto":
            mode = "login" if user_login else ("api_key" if api_key else "vertex")
        if mode == "login":
            return env  # la sesión de Google que el usuario abrió con `gemini`

        from backend.config import ROOT_DIR

        home = ROOT_DIR / "data" / "runtime" / "gemini-cli"
        (home / ".gemini").mkdir(parents=True, exist_ok=True)
        auth_type = "gemini-api-key" if mode == "api_key" else "vertex-ai"
        (home / ".gemini" / "settings.json").write_text(
            json.dumps({"security": {"auth": {"selectedType": auth_type}}}), encoding="utf-8")
        env["GEMINI_CLI_HOME"] = str(home)
        if mode == "api_key":
            if not api_key:
                raise ProviderError(self.name, "Falta la API key de Google (google_api) para Gemini CLI.",
                                    retriable=False)
            env["GEMINI_API_KEY"] = api_key
            return env

        from backend.providers import gcp_auth

        settings = gcp_auth.resolve_vertex_settings(config.get("providers", "vertex", default={}) or {})
        if not settings.project:
            raise ProviderError(self.name, "Gemini CLI con Vertex necesita un proyecto de Google Cloud "
                                "(gcloud config set project) o inicia sesión con `gemini`.", retriable=False)
        env.update({"GOOGLE_GENAI_USE_VERTEXAI": "true", "GOOGLE_CLOUD_PROJECT": settings.project,
                    "GOOGLE_CLOUD_LOCATION": settings.location or "global"})
        if settings.credentials_file:
            env["GOOGLE_APPLICATION_CREDENTIALS"] = settings.credentials_file
        return env

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
        if self.name == "gemini-cli":
            env.update(await asyncio.to_thread(self._gemini_env, workdir))
        process = await asyncio.create_subprocess_exec(
            *self._command(executable, model), cwd=workdir, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            # Grupo propio en Linux/macOS: al cortar se cierran también los hijos del CLI.
            start_new_session=os.name != "nt",
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
                await asyncio.to_thread(_kill_tree, process.pid)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(process.wait(), 5)
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
            elif self.name == "gemini-cli":
                if event.get("type") == "message" and event.get("role") == "assistant":
                    text = str(event.get("content") or "")
                    if text:
                        yield text
                elif event.get("type") == "error" or (event.get("type") == "result"
                                                       and event.get("status") not in (None, "success")):
                    detail = event.get("message") or (event.get("error") or {}).get("message") or "Error de Gemini CLI"
                    raise ProviderError(self.name, str(detail), model=model, retriable=False)
                elif event.get("type") == "result":
                    self._last_usage = _gemini_usage(event)
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


def _gemini_usage(event: dict[str, Any]) -> dict[str, Any]:
    stats = event.get("stats") or {}
    return {
        "input_tokens": int(stats.get("input_tokens") or 0),
        "output_tokens": int(stats.get("output_tokens") or 0),
        "thinking_tokens": 0,
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
            os.killpg(pid, signal.SIGKILL)
    except Exception as exc:
        logger.debug(f"cli provider: no se pudo cerrar {pid}: {exc}")
