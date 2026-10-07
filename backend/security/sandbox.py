"""
Sandbox execution environment for untrusted code.

Provides isolated execution with resource limits, tool whitelisting,
and comprehensive logging. Falls back to subprocess if Docker unavailable.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from backend.config import config


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False
    killed: bool = False
    sandbox_type: str = "subprocess"  # "docker" or "subprocess"


@dataclass
class SandboxConfig:
    timeout_seconds: int = 30
    max_memory_mb: int = 256
    max_output_bytes: int = 50_000
    allowed_tools: list[str] = field(default_factory=list)
    blocked_tools: list[str] = field(default_factory=list)
    network_enabled: bool = False
    writable_dirs: list[str] = field(default_factory=list)


# Commands that must NEVER run even in sandbox
BLOCKED_COMMANDS = [
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=/dev/zero",
    ":(){ :|:& };:", "fork bomb", "shutdown", "reboot",
    "init 0", "init 6", "format c:", "del /s /q c:\\",
    "reg delete", "wmic os", "net user", "netsh",
]


class SandboxExecutor:
    """Execute code in isolated sandbox environments."""

    def __init__(self) -> None:
        self._docker_available = self._check_docker()
        self._config = SandboxConfig(
            timeout_seconds=config.get("security", "sandbox", "timeout_seconds") or 30,
            max_memory_mb=config.get("security", "sandbox", "max_memory_mb") or 256,
            max_output_bytes=config.get("security", "sandbox", "max_output_bytes") or 50_000,
            network_enabled=config.get("security", "sandbox", "network_enabled") or False,
        )
        logger.info(
            f"Sandbox initialized: docker={'yes' if self._docker_available else 'no'}, "
            f"timeout={self._config.timeout_seconds}s"
        )

    @staticmethod
    def _check_docker() -> bool:
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _is_command_blocked(self, command: str) -> bool:
        cmd_lower = command.lower().strip()
        for blocked in BLOCKED_COMMANDS:
            if blocked.lower() in cmd_lower:
                return True
        return False

    async def execute_python(
        self,
        code: str,
        sandbox_config: SandboxConfig | None = None,
    ) -> SandboxResult:
        """Execute Python code in sandbox."""
        cfg = sandbox_config or self._config

        if self._docker_available:
            return await self._exec_docker_python(code, cfg)
        if not self._unisolated_fallback_allowed():
            return self._unavailable_result()
        return await self._exec_subprocess_python(code, cfg)

    async def execute_shell(
        self,
        command: str,
        sandbox_config: SandboxConfig | None = None,
    ) -> SandboxResult:
        """Execute shell command in sandbox."""
        cfg = sandbox_config or self._config

        if self._is_command_blocked(command):
            return SandboxResult(
                exit_code=1,
                stdout="",
                stderr=f"Command blocked by security policy: {command[:100]}",
                duration_ms=0,
                killed=True,
                sandbox_type="blocked",
            )

        if self._docker_available:
            return await self._exec_docker_shell(command, cfg)
        if not self._unisolated_fallback_allowed():
            return self._unavailable_result()
        return await self._exec_subprocess_shell(command, cfg)

    @staticmethod
    def _unisolated_fallback_allowed() -> bool:
        """
        Sin Docker no hay aislamiento real: un subproceso ve todo el disco y el
        keyring del usuario. Solo se permite si el usuario lo activa a sabiendas.
        """
        return bool(config.get("security", "sandbox", "allow_unisolated_fallback", default=False))

    @staticmethod
    def _unavailable_result() -> SandboxResult:
        return SandboxResult(
            exit_code=1,
            stdout="",
            stderr=(
                "Sandbox no disponible: se necesita Docker para ejecutar código aislado. "
                "Instala o inicia Docker Desktop. La ejecución sin aislamiento está "
                "desactivada (security.sandbox.allow_unisolated_fallback)."
            ),
            duration_ms=0,
            killed=True,
            sandbox_type="unavailable",
        )

    # ── Docker execution ─────────────────────────────────────────────

    async def _exec_docker_python(self, code: str, cfg: SandboxConfig) -> SandboxResult:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            script_path = f.name

        try:
            docker_cmd = [
                "docker", "run", "--rm",
                "--memory", f"{cfg.max_memory_mb}m",
                "--cpus", "1",
                "--pids-limit", "50",
                "--read-only",
                "--tmpfs", "/tmp:size=50m",
                "-v", f"{script_path}:/sandbox/script.py:ro",
            ]
            if not cfg.network_enabled:
                docker_cmd.extend(["--network", "none"])

            docker_cmd.extend(["python:3.11-slim", "python", "/sandbox/script.py"])

            return await self._run_process(docker_cmd, cfg, sandbox_type="docker")
        finally:
            Path(script_path).unlink(missing_ok=True)

    async def _exec_docker_shell(self, command: str, cfg: SandboxConfig) -> SandboxResult:
        docker_cmd = [
            "docker", "run", "--rm",
            "--memory", f"{cfg.max_memory_mb}m",
            "--cpus", "1",
            "--pids-limit", "50",
            "--read-only",
            "--tmpfs", "/tmp:size=50m",
        ]
        if not cfg.network_enabled:
            docker_cmd.extend(["--network", "none"])

        docker_cmd.extend(["alpine:latest", "sh", "-c", command])

        return await self._run_process(docker_cmd, cfg, sandbox_type="docker")

    # ── Subprocess fallback ──────────────────────────────────────────

    async def _exec_subprocess_python(self, code: str, cfg: SandboxConfig) -> SandboxResult:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            script_path = f.name

        try:
            # Strip sensitive env vars
            clean_env = {
                k: v for k, v in os.environ.items()
                if not any(secret in k.upper() for secret in
                           ["KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL"])
            }
            clean_env["PYTHONDONTWRITEBYTECODE"] = "1"

            return await self._run_process(
                [sys.executable, script_path],
                cfg,
                sandbox_type="unisolated",
                env=clean_env,
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

    async def _exec_subprocess_shell(self, command: str, cfg: SandboxConfig) -> SandboxResult:
        clean_env = {
            k: v for k, v in os.environ.items()
            if not any(secret in k.upper() for secret in
                       ["KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL"])
        }

        shell_cmd = ["cmd", "/c", command] if os.name == "nt" else ["sh", "-c", command]

        return await self._run_process(
            shell_cmd, cfg, sandbox_type="unisolated", env=clean_env,
        )

    # ── Common process runner ────────────────────────────────────────

    async def _run_process(
        self,
        cmd: list[str],
        cfg: SandboxConfig,
        sandbox_type: str = "subprocess",
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        start = time.monotonic()
        timed_out = False
        killed = False

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=cfg.timeout_seconds
                )
            except asyncio.TimeoutError:
                timed_out = True
                proc.kill()
                stdout_bytes, stderr_bytes = await proc.communicate()

            duration_ms = (time.monotonic() - start) * 1000

            # Truncate output
            stdout = stdout_bytes.decode("utf-8", errors="replace")[:cfg.max_output_bytes]
            stderr = stderr_bytes.decode("utf-8", errors="replace")[:cfg.max_output_bytes]

            return SandboxResult(
                exit_code=proc.returncode or 0,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                timed_out=timed_out,
                killed=killed,
                sandbox_type=sandbox_type,
            )

        except Exception as e:
            duration_ms = (time.monotonic() - start) * 1000
            return SandboxResult(
                exit_code=1,
                stdout="",
                stderr=f"Sandbox execution error: {e}",
                duration_ms=duration_ms,
                killed=True,
                sandbox_type=sandbox_type,
            )

    # ── Info ──────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        return {
            "docker_available": self._docker_available,
            "timeout_seconds": self._config.timeout_seconds,
            "max_memory_mb": self._config.max_memory_mb,
            "network_enabled": self._config.network_enabled,
        }


# ── Singleton ────────────────────────────────────────────────────────────

_sandbox: SandboxExecutor | None = None


def get_sandbox() -> SandboxExecutor:
    global _sandbox
    if _sandbox is None:
        _sandbox = SandboxExecutor()
    return _sandbox
