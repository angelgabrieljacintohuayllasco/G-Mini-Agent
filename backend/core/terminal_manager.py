"""
Multi-shell detection and execution manager for Phase 4.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

from backend.config import config
from backend.core.exec_approvals import evaluate_command, get_exec_approvals_summary

_MAX_OUTPUT_BYTES = 2 * 1024 * 1024


def _default_timeout() -> float:
    try:
        return max(5.0, float(config.get("terminals", "timeout_seconds", default=120)))
    except (TypeError, ValueError):
        return 120.0


def _new_process_group_kwargs() -> dict[str, Any]:
    """Proceso en su propio grupo para poder matar también a sus hijos."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                capture_output=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            import signal

            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except Exception as exc:
        logger.debug(f"No se pudo matar el árbol del proceso {process.pid}: {exc}")
        try:
            process.kill()
        except Exception:
            pass


async def _read_capped(stream: asyncio.StreamReader | None, chunks: list[bytes]) -> None:
    """Lee la salida sin bloquear al proceso; guarda hasta 2 MB (cabeza y cola)."""
    if stream is None:
        return
    total = 0
    while True:
        data = await stream.read(65536)
        if not data:
            return
        total += len(data)
        chunks.append(data)
        if total > _MAX_OUTPUT_BYTES:
            # Conservar el inicio y lo último: se descarta lo del medio.
            while sum(len(c) for c in chunks) > _MAX_OUTPUT_BYTES and len(chunks) > 2:
                chunks.pop(1)


@dataclass(frozen=True)
class ShellInfo:
    key: str
    name: str
    executable: str
    kind: str
    available: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "executable": self.executable,
            "kind": self.kind,
            "available": self.available,
            "metadata": self.metadata,
        }


@dataclass
class TerminalSession:
    id: str
    shell_key: str
    shell_name: str
    command: str
    cwd: str
    status: str = "running"
    pid: int | None = None
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    finished_at: str | None = None
    return_code: int | None = None
    output: str = ""
    task_type: str = "generic"

    def to_dict(self) -> dict[str, Any]:
        preview = self.output.strip()
        if len(preview) > 240:
            preview = preview[:237] + "..."
        return {
            "id": self.id,
            "shell_key": self.shell_key,
            "shell_name": self.shell_name,
            "command": self.command,
            "cwd": self.cwd,
            "status": self.status,
            "pid": self.pid,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "return_code": self.return_code,
            "output_preview": preview,
            "task_type": self.task_type,
        }


class TerminalManager:
    def __init__(self, max_sessions: int = 10):
        self._max_sessions = max_sessions
        self._shells: dict[str, ShellInfo] = {}
        self._sessions: dict[str, TerminalSession] = {}

    async def initialize(self) -> None:
        self._shells = await asyncio.to_thread(self._detect_shells)
        logger.info(f"TerminalManager inicializado con {len(self._shells)} terminales detectadas")

    def list_shells(self) -> list[dict[str, Any]]:
        return [shell.to_dict() for shell in self._shells.values()]

    def list_sessions(self) -> list[dict[str, Any]]:
        items = sorted(self._sessions.values(), key=lambda item: item.started_at, reverse=True)
        return [item.to_dict() for item in items]

    def active_count(self) -> int:
        return sum(1 for session in self._sessions.values() if session.status == "running")

    def get_exec_approvals_summary(self) -> dict[str, Any]:
        return get_exec_approvals_summary()

    def choose_shell(self, command: str, task_type: str = "auto", preferred: str | None = None) -> ShellInfo:
        if preferred and preferred in self._shells:
            return self._shells[preferred]

        available = self._shells
        command_lower = command.lower()
        task = (task_type or "auto").lower()

        if task == "git" or command_lower.startswith("git "):
            return self._first_available(["gitbash", "wsl_ubuntu", "wsl", "pwsh", "powershell", "cmd"])
        if task in {"node", "javascript"} or any(token in command_lower for token in ["npm ", "pnpm ", "yarn ", "node "]):
            return self._first_available(["wsl_ubuntu", "wsl", "gitbash", "pwsh", "powershell"])
        if task in {"docker", "devops"} or any(token in command_lower for token in ["docker ", "docker-compose", "kubectl ", "terraform "]):
            return self._first_available(["wsl_ubuntu", "wsl", "pwsh", "powershell"])
        if task == "windows_admin" or any(token in command_lower for token in ["winget ", "choco ", ".ps1", "powershell "]):
            return self._first_available(["pwsh", "powershell", "cmd"])
        if task == "python" or "python " in command_lower or "pip " in command_lower:
            return self._first_available(["pwsh", "powershell", "wsl_ubuntu", "wsl", "cmd"])
        if any(token in command_lower for token in ["apt ", "sudo ", "bash ", "sh "]):
            return self._first_available(["wsl_ubuntu", "wsl", "gitbash", "pwsh"])
        return self._first_available(["pwsh", "powershell", "cmd", "gitbash", "wsl_ubuntu", "wsl"])

    async def run_command(
        self,
        command: str,
        cwd: str | None = None,
        shell_key: str | None = None,
        task_type: str = "auto",
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        approval = evaluate_command(command)
        if not approval.allowed:
            raise PermissionError(
                f"Exec approval bloqueó el comando para host {approval.host_key}: {approval.reason}"
            )

        if self.active_count() >= self._max_sessions:
            raise RuntimeError(f"Limite de terminales activas alcanzado ({self._max_sessions})")

        shell = self.choose_shell(command=command, task_type=task_type, preferred=shell_key)
        session = TerminalSession(
            id=f"term_{uuid4().hex[:8]}",
            shell_key=shell.key,
            shell_name=shell.name,
            command=command,
            cwd=cwd or str(Path.cwd()),
            task_type=task_type,
        )
        self._sessions[session.id] = session

        cmd_list, effective_cwd = self._build_command(shell, command, session.cwd)
        timeout = float(timeout_seconds or _default_timeout())
        logger.info(f"Terminal session {session.id} usando {shell.key} (timeout {timeout:.0f}s): {command}")
        process = await asyncio.create_subprocess_exec(
            *cmd_list,
            cwd=effective_cwd,
            stdin=asyncio.subprocess.DEVNULL,  # un comando que pide entrada no debe colgar al agente
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **_new_process_group_kwargs(),
        )
        session.pid = process.pid
        chunks: list[bytes] = []
        reader = asyncio.create_task(_read_capped(process.stdout, chunks))
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            _kill_process_tree(process)
            logger.warning(f"Terminal session {session.id} superó {timeout:.0f}s; proceso detenido")
        except asyncio.CancelledError:
            # Stop del usuario: matar el árbol completo (cmd/npm dejan hijos vivos).
            _kill_process_tree(process)
            session.status = "cancelled"
            session.finished_at = datetime.now().isoformat()
            reader.cancel()
            raise
        try:
            await asyncio.wait_for(reader, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            reader.cancel()

        session.return_code = process.returncode
        session.finished_at = datetime.now().isoformat()
        session.output = b"".join(chunks).decode("utf-8", errors="replace")
        if timed_out:
            session.status = "timeout"
        else:
            session.status = "completed" if process.returncode == 0 else "failed"
        logger.info(
            f"Terminal session {session.id} finalizada | shell={shell.key} rc={session.return_code} status={session.status}"
        )
        result = session.to_dict()
        result.update({
            "output": session.output,
            "stdout": session.output,
            "exit_code": session.return_code,
            "timed_out": timed_out,
        })
        return result

    def _first_available(self, keys: list[str]) -> ShellInfo:
        for key in keys:
            if key in self._shells:
                return self._shells[key]
            if key == "wsl":
                for shell_key, shell in self._shells.items():
                    if shell.kind == "wsl":
                        return shell
        if self._shells:
            return next(iter(self._shells.values()))
        raise RuntimeError("No hay terminales disponibles")

    def _build_command(self, shell: ShellInfo, command: str, cwd: str) -> tuple[list[str], str | None]:
        if shell.kind == "powershell":
            return [shell.executable, "-NoProfile", "-Command", command], cwd
        if shell.kind == "cmd":
            return [shell.executable, "/C", command], cwd
        if shell.kind == "gitbash":
            bash_cmd = command
            if cwd:
                bash_cmd = f"cd {self._quote_posix(self._windows_to_msys(cwd))} && {command}"
            return [shell.executable, "-lc", bash_cmd], None
        if shell.kind == "wsl":
            distro = shell.metadata.get("distro")
            bash_cmd = command
            if cwd:
                bash_cmd = f"cd {self._quote_posix(self._windows_to_posix(cwd))} && {command}"
            return ["wsl", "-d", distro, "--", "bash", "-lc", bash_cmd], None
        raise RuntimeError(f"Terminal no soportada: {shell.key}")

    def _detect_shells(self) -> dict[str, ShellInfo]:
        shells: dict[str, ShellInfo] = {}

        pwsh = shutil.which("pwsh")
        if pwsh:
            shells["pwsh"] = ShellInfo("pwsh", "PowerShell 7", pwsh, "powershell")

        powershell = shutil.which("powershell")
        if powershell:
            shells["powershell"] = ShellInfo("powershell", "Windows PowerShell", powershell, "powershell")

        cmd = os.environ.get("ComSpec") or shutil.which("cmd")
        if cmd:
            shells["cmd"] = ShellInfo("cmd", "Command Prompt", cmd, "cmd")

        git_bash = shutil.which("bash")
        git_bash_name = ""
        if git_bash and "git" in git_bash.lower():
            git_bash_name = "Git Bash"
        else:
            git_candidate = Path(os.environ.get("ProgramFiles", "")) / "Git" / "bin" / "bash.exe"
            if git_candidate.exists():
                git_bash = str(git_candidate)
                git_bash_name = "Git Bash"
        if git_bash and git_bash_name:
            shells["gitbash"] = ShellInfo("gitbash", git_bash_name, git_bash, "gitbash")

        wsl = shutil.which("wsl")
        if wsl:
            distros = self._detect_wsl_distros()
            for distro in distros:
                key = f"wsl_{re.sub(r'[^a-z0-9]+', '_', distro.lower()).strip('_')}"
                shells[key] = ShellInfo(key, f"WSL {distro}", wsl, "wsl", metadata={"distro": distro})

        return shells

    def _detect_wsl_distros(self) -> list[str]:
        try:
            result = subprocess.run(
                ["wsl", "--list", "--quiet"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            lines = []
            for raw_line in result.stdout.splitlines():
                cleaned = raw_line.replace("\x00", "").replace("*", "").strip()
                if cleaned:
                    lines.append(cleaned)
            return lines
        except Exception:
            return []

    def _windows_to_posix(self, path: str) -> str:
        candidate = Path(path)
        drive = candidate.drive.rstrip(":").lower()
        rest = str(candidate).replace("\\", "/")
        if drive:
            rest = rest[len(candidate.drive):].lstrip("/\\")
            return f"/mnt/{drive}/{rest}"
        return str(candidate).replace("\\", "/")

    def _windows_to_msys(self, path: str) -> str:
        """Convert Windows path to MSYS/Git-Bash format: C:\\Users -> /c/Users"""
        candidate = Path(path)
        drive = candidate.drive.rstrip(":").lower()
        rest = str(candidate).replace("\\", "/")
        if drive:
            rest = rest[len(candidate.drive):].lstrip("/\\")
            return f"/{drive}/{rest}"
        return str(candidate).replace("\\", "/")

    def _quote_posix(self, path: str) -> str:
        escaped = path.replace("'", "'\"'\"'")
        return f"'{escaped}'"
