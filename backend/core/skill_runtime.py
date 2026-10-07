"""
G-Mini Agent — Skill Runtime.

Ejecuta las tools de las skills con manifiesto (skill.yaml / skill.json) como
subprocesos, con el protocolo que ya usan los scripts incluidos:

- Entrada: JSON en un archivo temporal cuya ruta va en GMINI_SKILL_INPUT:
  {"tool", "skill_id", "input": {...}} y además los campos de input en el
  primer nivel (los scripts viejos los leen ahí; los nuevos, dentro de "input").
- Salida: JSON en el archivo GMINI_SKILL_OUTPUT o, si no lo escriben, en stdout.
- Entorno mínimo: las tools no heredan claves ni el token de sesión del
  backend; solo reciben las variables que el manifiesto declara en
  requires.env, tomadas del entorno o del keyring ("skill_env:<VAR>").
- Timeout con cierre de todo el árbol de procesos y salida con tope.

Todo es bloqueante: se llama desde un hilo (asyncio.to_thread).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from loguru import logger

from backend.config import config

DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 600
MAX_STREAM_CHARS = 64_000
SAFE_ENV_KEYS = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME",
    "USERPROFILE", "USERNAME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL",
)
_INTERPRETERS = {".py": [sys.executable], ".ps1": ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"],
                 ".js": ["node"], ".sh": ["bash"]}


class SkillRuntimeError(RuntimeError):
    pass


def env_vault_name(var: str) -> str:
    return f"skill_env:{var}"


def _clip(text: str) -> str:
    if len(text) <= MAX_STREAM_CHARS:
        return text
    half = MAX_STREAM_CHARS // 2
    return f"{text[:half]}\n[... {len(text) - MAX_STREAM_CHARS} caracteres omitidos ...]\n{text[-half:]}"


def _kill_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            import signal

            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except Exception as exc:
        logger.debug(f"skill runtime: no se pudo cerrar el árbol de {process.pid}: {exc}")
    try:
        process.kill()
    except Exception:
        pass


class SkillRuntime:
    """Ejecuta tools de skills registradas en SkillRegistry."""

    def __init__(self, registry=None):
        self._registry = registry

    @property
    def registry(self):
        if self._registry is None:
            from backend.core.skill_registry import SkillRegistry

            self._registry = SkillRegistry()
        return self._registry

    # ── API ──────────────────────────────────────────────────────────

    def run_tool(
        self,
        skill_id: str,
        tool: str,
        input_data: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        base = {"success": False, "skill_id": skill_id, "tool": tool, "exit_code": None, "data": None,
                "stdout": "", "stderr": "", "timed_out": False, "duration_ms": 0, "command": [], "cwd": "",
                "error": None}
        try:
            skill, spec = self._resolve(skill_id, tool)
            command, cwd = self._command(skill, spec)
        except (SkillRuntimeError, KeyError) as exc:
            return {**base, "error": str(exc).strip("'")}
        timeout = self._timeout(timeout_seconds, spec.get("timeout_seconds"))
        payload = dict(input_data or {})
        payload.update({"tool": spec["name"], "skill_id": skill["id"], "input": dict(input_data or {})})
        return self._execute(base, skill, command, cwd, payload, timeout)

    # ── Resolución ───────────────────────────────────────────────────

    def _resolve(self, skill_id: str, tool: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if not config.get("skills", "enabled", default=True):
            raise SkillRuntimeError("Las skills están desactivadas en la configuración.")
        try:
            skill = self.registry.get_skill(skill_id)
        except KeyError:
            raise SkillRuntimeError(f"No existe la skill '{skill_id}'.") from None
        if not skill.get("enabled", True):
            raise SkillRuntimeError(f"La skill '{skill['id']}' está desactivada.")
        for spec in skill.get("tools") or []:
            if spec.get("name") == tool:
                return skill, spec
        names = ", ".join(t.get("name", "") for t in skill.get("tools") or []) or "ninguna"
        raise SkillRuntimeError(f"La skill '{skill['id']}' no tiene la tool '{tool}' (disponibles: {names}).")

    @staticmethod
    def _command(skill: dict[str, Any], spec: dict[str, Any]) -> tuple[list[str], Path]:
        root = Path(skill["root_path"]).resolve()
        if spec.get("script"):
            script = (root / spec["script"]).resolve()
            if root not in script.parents:
                raise SkillRuntimeError(f"El script de '{spec['name']}' está fuera de la carpeta de la skill.")
            if not script.is_file():
                raise SkillRuntimeError(f"No existe el script {spec['script']} de la skill '{skill['id']}'.")
            interpreter = _INTERPRETERS.get(script.suffix.lower())
            if interpreter is None:
                raise SkillRuntimeError(f"Tipo de script no soportado: {script.suffix}")
            if not Path(interpreter[0]).is_file() and shutil.which(interpreter[0]) is None:
                raise SkillRuntimeError(f"No se encontró '{interpreter[0]}' para ejecutar {script.name}.")
            return [*interpreter, str(script)], root
        command = spec.get("command")
        if isinstance(command, list) and command:
            return [str(part) for part in command], root
        raise SkillRuntimeError(f"La tool '{spec['name']}' no declara script ni command.")

    @staticmethod
    def _timeout(requested: Any, declared: Any) -> int:
        for value in (requested, declared, config.get("skills", "default_timeout_seconds", default=None)):
            try:
                if value not in (None, ""):
                    return max(1, min(int(value), MAX_TIMEOUT_SECONDS))
            except (TypeError, ValueError):
                continue
        return DEFAULT_TIMEOUT_SECONDS

    @staticmethod
    def _env(skill: dict[str, Any], workdir: Path, input_path: Path, output_path: Path) -> dict[str, str]:
        env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ}
        missing = []
        for var in skill.get("requires_env") or []:
            value = os.environ.get(var) or config.get_api_key(env_vault_name(var))
            if value:
                env[var] = value
            else:
                missing.append(var)
        env.update({
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "GMINI_SKILL_ID": str(skill["id"]),
            "GMINI_SKILL_DIR": str(Path(skill["root_path"]).resolve()),
            "GMINI_SKILL_WORKDIR": str(workdir),
            "GMINI_SKILL_INPUT": str(input_path),
            "GMINI_SKILL_OUTPUT": str(output_path),
        })
        if missing:
            env["GMINI_SKILL_MISSING_ENV"] = ",".join(missing)
        return env

    # ── Ejecución ────────────────────────────────────────────────────

    def _execute(
        self,
        base: dict[str, Any],
        skill: dict[str, Any],
        command: list[str],
        cwd: Path,
        payload: dict[str, Any],
        timeout: int,
    ) -> dict[str, Any]:
        workdir = Path(tempfile.mkdtemp(prefix=f"gmini-skill-{skill['id']}-"))
        input_path, output_path = workdir / "input.json", workdir / "output.json"
        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        env = self._env(skill, workdir, input_path, output_path)
        result = {**base, "command": command, "cwd": str(cwd)}
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(process)
                stdout, stderr = process.communicate()
                result["timed_out"] = True
            result.update({
                "exit_code": process.returncode,
                "stdout": _clip(stdout or ""),
                "stderr": _clip(stderr or ""),
                "duration_ms": int((time.monotonic() - started) * 1000),
            })
            data = self._read_output(output_path, stdout or "")
            result["data"] = data
            error = self._error_from(result, data, timeout, env)
            result["error"] = error
            result["success"] = error is None
            return result
        except OSError as exc:
            return {**result, "error": f"No se pudo iniciar la tool: {exc}"}
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _read_output(output_path: Path, stdout: str) -> dict[str, Any] | None:
        raw = ""
        if output_path.is_file():
            raw = output_path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            raw = stdout.strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Algunas tools imprimen logs antes del JSON: probar con la última línea.
            try:
                data = json.loads(raw.splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                return {"text": _clip(raw)}
        return data if isinstance(data, dict) else {"result": data}

    @staticmethod
    def _error_from(result: dict[str, Any], data: dict[str, Any] | None, timeout: int, env: dict[str, str]) -> str | None:
        if result["timed_out"]:
            return f"La tool superó {timeout} s y se detuvo."
        if isinstance(data, dict) and data.get("error"):
            missing = env.get("GMINI_SKILL_MISSING_ENV")
            hint = f" (faltan variables: {missing}; configúralas en Ajustes > Skills)" if missing else ""
            return f"{data['error']}{hint}"
        if result["exit_code"] not in (0, None):
            tail = (result["stderr"] or result["stdout"]).strip().splitlines()[-3:]
            return f"La tool terminó con código {result['exit_code']}: " + " | ".join(tail)
        return None


def get_skill_runtime(registry=None) -> SkillRuntime:
    return SkillRuntime(registry)
