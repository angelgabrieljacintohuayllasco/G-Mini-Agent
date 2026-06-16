"""
G-Mini Agent - Scheduler persistente.
Base de tareas programadas para jobs de skills y MCP tools.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger

from backend.config import ROOT_DIR, config
from backend.core.gateway_service import get_gateway
from backend.core.mcp_registry import get_mcp_registry
from backend.core.mcp_runtime import MCPRuntime
from backend.core.skill_runtime import SkillRuntime

try:
    from croniter import croniter
except Exception:  # pragma: no cover - dependencia opcional
    croniter = None

DB_DIR = ROOT_DIR / "data"
DB_DIR.mkdir(exist_ok=True)
DEFAULT_DB_PATH = DB_DIR / "scheduler.db"
DEFAULT_MAX_RETRIES = 0
DEFAULT_RETRY_BACKOFF_SECONDS = 30
DEFAULT_RETRY_BACKOFF_MULTIPLIER = 2.0
DEFAULT_HEARTBEAT_KEY = "system"
DEFAULT_CHECKPOINT_LIMIT = 100
RECOVERY_ERROR_MESSAGE = "Interrumpido por reinicio o crash del scheduler."


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "si", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _resolve_scheduler_db_path() -> Path:
    configured = str(
        config.get("scheduler", "db_path", default=str(DEFAULT_DB_PATH))
    ).strip()
    if not configured:
        return DEFAULT_DB_PATH
    candidate = Path(configured)
    if not candidate.is_absolute():
        candidate = ROOT_DIR / candidate
    return candidate


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _normalize_signal_name(value: Any, *, default: str = "") -> str:
    normalized = str(value or "").strip()
    return normalized.lower() if normalized else default


def _normalize_webhook_path(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = raw.replace("\\", "/").strip("/")
    return normalized.lower()


def _safe_json_loads(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


class SchedulerService:
    def __init__(
        self,
        db_path: Path | None = None,
        skill_runtime: SkillRuntime | None = None,
        mcp_runtime: MCPRuntime | None = None,
    ):
        self._db_path = Path(db_path) if db_path else _resolve_scheduler_db_path()
        self._skill_runtime = skill_runtime or SkillRuntime()
        self._mcp_runtime = mcp_runtime or MCPRuntime(get_mcp_registry())
        self._enabled = _coerce_bool(
            config.get("scheduler", "enabled", default=True),
            default=True,
        )
        try:
            self._poll_interval_seconds = max(
                0.5,
                float(config.get("scheduler", "poll_interval_seconds", default=2.0)),
            )
        except (TypeError, ValueError):
            self._poll_interval_seconds = 2.0
        self._initialized = False
        self._running = False
        self._loop_task: asyncio.Task | None = None
        self._active_jobs: set[str] = set()
        self._last_recovery_summary: dict[str, Any] = {
            "checked_at": None,
            "interrupted_runs": 0,
            "rescheduled_jobs": 0,
            "retry_scheduled_jobs": 0,
            "recovered_run_ids": [],
        }

    async def initialize(self) -> None:
        if self._initialized:
            if self._enabled and not self._running:
                self._running = True
                self._loop_task = asyncio.create_task(self._run_loop())
                logger.info(f"SchedulerService reanudado: {self._db_path}")
            return

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    job_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    interval_seconds INTEGER,
                    cron_expression TEXT,
                    event_name TEXT DEFAULT '',
                    webhook_path TEXT DEFAULT '',
                    webhook_secret TEXT DEFAULT '',
                    heartbeat_key TEXT DEFAULT 'system',
                    heartbeat_interval_seconds INTEGER,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    max_retries INTEGER NOT NULL DEFAULT 0,
                    retry_backoff_seconds INTEGER NOT NULL DEFAULT 30,
                    retry_backoff_multiplier REAL NOT NULL DEFAULT 2.0,
                    retry_attempt INTEGER NOT NULL DEFAULT 0,
                    next_run_at TEXT,
                    last_signal_at TEXT,
                    last_run_at TEXT,
                    last_error TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_runs (
                    run_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    trigger_source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    duration_ms INTEGER DEFAULT 0,
                    result_json TEXT DEFAULT '{}',
                    error TEXT DEFAULT '',
                    FOREIGN KEY(job_id) REFERENCES scheduled_jobs(job_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    run_id TEXT,
                    checkpoint_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress REAL DEFAULT 0,
                    message TEXT DEFAULT '',
                    payload_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES scheduled_jobs(job_id),
                    FOREIGN KEY(run_id) REFERENCES scheduled_runs(run_id)
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_next_run ON scheduled_jobs (enabled, next_run_at)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_runs_job ON scheduled_runs (job_id, started_at DESC)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_checkpoints_job ON scheduled_checkpoints (job_id, created_at DESC)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_checkpoints_run ON scheduled_checkpoints (run_id, created_at DESC)"
            )
            await self._ensure_job_columns(db)
            self._last_recovery_summary = await self._recover_interrupted_runs(db)
            await db.commit()
        self._initialized = True

        if not self._enabled:
            logger.info("SchedulerService deshabilitado por configuracion")
            return

        if not self._running:
            self._running = True
            self._loop_task = asyncio.create_task(self._run_loop())
        logger.info(f"SchedulerService inicializado: {self._db_path}")

    async def shutdown(self) -> None:
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

    async def reload_config(self) -> None:
        self._enabled = _coerce_bool(
            config.get("scheduler", "enabled", default=True),
            default=True,
        )
        try:
            self._poll_interval_seconds = max(
                0.5,
                float(config.get("scheduler", "poll_interval_seconds", default=2.0)),
            )
        except (TypeError, ValueError):
            self._poll_interval_seconds = 2.0

        if not self._enabled:
            await self.shutdown()
            logger.info("SchedulerService recargado: deshabilitado")
            return

        await self.initialize()
        logger.info(
            f"SchedulerService recargado: enabled=True poll={self._poll_interval_seconds}s"
        )

    async def list_jobs(self) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM scheduled_jobs ORDER BY created_at DESC"
            ) as cursor:
                async for row in cursor:
                    jobs.append(self._row_to_job(dict(row)))
        return jobs

    async def get_job(self, job_id: str) -> dict[str, Any]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM scheduled_jobs WHERE job_id = ?",
                (job_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    raise KeyError(job_id)
                return self._row_to_job(dict(row))

    async def create_job(
        self,
        *,
        name: str,
        task_type: str,
        payload: dict[str, Any],
        trigger_type: str,
        interval_seconds: int | None = None,
        cron_expression: str | None = None,
        event_name: str | None = None,
        webhook_path: str | None = None,
        webhook_secret: str | None = None,
        heartbeat_key: str | None = None,
        heartbeat_interval_seconds: int | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_seconds: int = DEFAULT_RETRY_BACKOFF_SECONDS,
        retry_backoff_multiplier: float = DEFAULT_RETRY_BACKOFF_MULTIPLIER,
        enabled: bool = True,
    ) -> dict[str, Any]:
        normalized_task_type = str(task_type or "").strip().lower()
        normalized_trigger = str(trigger_type or "").strip().lower()
        normalized_trigger_config = self._normalize_trigger_config(
            trigger_type=normalized_trigger,
            event_name=event_name,
            webhook_path=webhook_path,
            webhook_secret=webhook_secret,
            heartbeat_key=heartbeat_key,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
        self._validate_job_definition(
            normalized_task_type,
            payload,
            normalized_trigger,
            interval_seconds,
            cron_expression,
            normalized_trigger_config["event_name"],
            normalized_trigger_config["webhook_path"],
            normalized_trigger_config["heartbeat_key"],
            normalized_trigger_config["heartbeat_interval_seconds"],
        )
        normalized_retry_policy = self._validate_retry_policy(
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            retry_backoff_multiplier=retry_backoff_multiplier,
        )

        now = _utcnow()
        next_run = self._compute_next_run(
            trigger_type=normalized_trigger,
            interval_seconds=interval_seconds,
            cron_expression=cron_expression,
            heartbeat_interval_seconds=normalized_trigger_config["heartbeat_interval_seconds"],
            from_dt=now,
        ) if enabled else None

        job_id = f"job_{uuid.uuid4().hex[:12]}"
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO scheduled_jobs (
                    job_id, name, task_type, payload_json, trigger_type,
                    interval_seconds, cron_expression, event_name, webhook_path,
                    webhook_secret, heartbeat_key, heartbeat_interval_seconds,
                    enabled, max_retries, retry_backoff_seconds,
                    retry_backoff_multiplier, retry_attempt, next_run_at,
                    last_signal_at, last_run_at, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    str(name or "").strip() or job_id,
                    normalized_task_type,
                    json.dumps(payload or {}, ensure_ascii=False),
                    normalized_trigger,
                    interval_seconds,
                    cron_expression,
                    normalized_trigger_config["event_name"],
                    normalized_trigger_config["webhook_path"],
                    normalized_trigger_config["webhook_secret"],
                    normalized_trigger_config["heartbeat_key"],
                    normalized_trigger_config["heartbeat_interval_seconds"],
                    1 if enabled else 0,
                    normalized_retry_policy["max_retries"],
                    normalized_retry_policy["retry_backoff_seconds"],
                    normalized_retry_policy["retry_backoff_multiplier"],
                    0,
                    _serialize_dt(next_run),
                    None,
                    None,
                    "",
                    _serialize_dt(now),
                    _serialize_dt(now),
                ),
            )
            await db.commit()
        return await self.get_job(job_id)

    async def update_job(
        self,
        job_id: str,
        *,
        name: str | None = None,
        payload: dict[str, Any] | None = None,
        interval_seconds: int | None = None,
        cron_expression: str | None = None,
        event_name: str | None = None,
        webhook_path: str | None = None,
        webhook_secret: str | None = None,
        heartbeat_key: str | None = None,
        heartbeat_interval_seconds: int | None = None,
        max_retries: int | None = None,
        retry_backoff_seconds: int | None = None,
        retry_backoff_multiplier: float | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        current = await self.get_job(job_id)
        current_trigger_config = self._normalize_trigger_config(
            trigger_type=current["trigger_type"],
            event_name=current.get("event_name"),
            webhook_path=current.get("webhook_path"),
            webhook_secret=current.get("webhook_secret"),
            heartbeat_key=current.get("heartbeat_key"),
            heartbeat_interval_seconds=current.get("heartbeat_interval_seconds"),
        )
        normalized_trigger_config = self._normalize_trigger_config(
            trigger_type=current["trigger_type"],
            event_name=event_name if event_name is not None else current_trigger_config["event_name"],
            webhook_path=webhook_path if webhook_path is not None else current_trigger_config["webhook_path"],
            webhook_secret=webhook_secret if webhook_secret is not None else current_trigger_config["webhook_secret"],
            heartbeat_key=heartbeat_key if heartbeat_key is not None else current_trigger_config["heartbeat_key"],
            heartbeat_interval_seconds=(
                heartbeat_interval_seconds
                if heartbeat_interval_seconds is not None
                else current_trigger_config["heartbeat_interval_seconds"]
            ),
        )
        merged = {
            "name": name if name is not None else current["name"],
            "task_type": current["task_type"],
            "payload": payload if payload is not None else current["payload"],
            "trigger_type": current["trigger_type"],
            "interval_seconds": interval_seconds if interval_seconds is not None else current.get("interval_seconds"),
            "cron_expression": cron_expression if cron_expression is not None else current.get("cron_expression"),
            "event_name": normalized_trigger_config["event_name"],
            "webhook_path": normalized_trigger_config["webhook_path"],
            "webhook_secret": normalized_trigger_config["webhook_secret"],
            "heartbeat_key": normalized_trigger_config["heartbeat_key"],
            "heartbeat_interval_seconds": normalized_trigger_config["heartbeat_interval_seconds"],
            "max_retries": max_retries if max_retries is not None else current.get("max_retries", DEFAULT_MAX_RETRIES),
            "retry_backoff_seconds": (
                retry_backoff_seconds
                if retry_backoff_seconds is not None
                else current.get("retry_backoff_seconds", DEFAULT_RETRY_BACKOFF_SECONDS)
            ),
            "retry_backoff_multiplier": (
                retry_backoff_multiplier
                if retry_backoff_multiplier is not None
                else current.get("retry_backoff_multiplier", DEFAULT_RETRY_BACKOFF_MULTIPLIER)
            ),
            "enabled": current["enabled"] if enabled is None else enabled,
        }
        self._validate_job_definition(
            merged["task_type"],
            merged["payload"],
            merged["trigger_type"],
            merged["interval_seconds"],
            merged["cron_expression"],
            merged["event_name"],
            merged["webhook_path"],
            merged["heartbeat_key"],
            merged["heartbeat_interval_seconds"],
        )
        normalized_retry_policy = self._validate_retry_policy(
            max_retries=merged["max_retries"],
            retry_backoff_seconds=merged["retry_backoff_seconds"],
            retry_backoff_multiplier=merged["retry_backoff_multiplier"],
        )

        next_run = self._compute_next_run(
            trigger_type=merged["trigger_type"],
            interval_seconds=merged["interval_seconds"],
            cron_expression=merged["cron_expression"],
            heartbeat_interval_seconds=merged["heartbeat_interval_seconds"],
            from_dt=_utcnow(),
        ) if merged["enabled"] else None

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                UPDATE scheduled_jobs
                SET name = ?, payload_json = ?, interval_seconds = ?, cron_expression = ?,
                    event_name = ?, webhook_path = ?, webhook_secret = ?,
                    heartbeat_key = ?, heartbeat_interval_seconds = ?, enabled = ?,
                    max_retries = ?, retry_backoff_seconds = ?, retry_backoff_multiplier = ?,
                    retry_attempt = 0, next_run_at = ?, updated_at = ?, last_error = ''
                WHERE job_id = ?
                """,
                (
                    merged["name"],
                    json.dumps(merged["payload"], ensure_ascii=False),
                    merged["interval_seconds"],
                    merged["cron_expression"],
                    merged["event_name"],
                    merged["webhook_path"],
                    merged["webhook_secret"],
                    merged["heartbeat_key"],
                    merged["heartbeat_interval_seconds"],
                    1 if merged["enabled"] else 0,
                    normalized_retry_policy["max_retries"],
                    normalized_retry_policy["retry_backoff_seconds"],
                    normalized_retry_policy["retry_backoff_multiplier"],
                    _serialize_dt(next_run),
                    _serialize_dt(_utcnow()),
                    job_id,
                ),
            )
            await db.commit()
        return await self.get_job(job_id)

    async def delete_job(self, job_id: str) -> dict[str, Any]:
        job = await self.get_job(job_id)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM scheduled_checkpoints WHERE job_id = ?", (job_id,))
            await db.execute("DELETE FROM scheduled_runs WHERE job_id = ?", (job_id,))
            await db.execute("DELETE FROM scheduled_jobs WHERE job_id = ?", (job_id,))
            await db.commit()
        return {"deleted": True, "job": job}

    async def list_runs(self, job_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        query = "SELECT * FROM scheduled_runs"
        params: tuple[Any, ...]
        if job_id:
            query += " WHERE job_id = ?"
            params = (job_id, limit)
            query += " ORDER BY started_at DESC LIMIT ?"
        else:
            params = (limit,)
            query += " ORDER BY started_at DESC LIMIT ?"

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                async for row in cursor:
                    runs.append(self._row_to_run(dict(row)))
        return runs

    async def list_checkpoints(
        self,
        *,
        job_id: str | None = None,
        run_id: str | None = None,
        limit: int = DEFAULT_CHECKPOINT_LIMIT,
    ) -> list[dict[str, Any]]:
        checkpoints: list[dict[str, Any]] = []
        query = "SELECT * FROM scheduled_checkpoints"
        params: list[Any] = []
        clauses: list[str] = []
        if job_id:
            clauses.append("job_id = ?")
            params.append(job_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, tuple(params)) as cursor:
                async for row in cursor:
                    checkpoints.append(self._row_to_checkpoint(dict(row)))
        return checkpoints

    def get_recovery_summary(self) -> dict[str, Any]:
        return dict(self._last_recovery_summary)

    async def run_job_now(self, job_id: str) -> dict[str, Any]:
        job = await self.get_job(job_id)
        return await self._execute_job(job, trigger_source="manual")

    async def emit_event(
        self,
        event_name: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_event = _normalize_signal_name(event_name)
        if not normalized_event:
            raise ValueError("event_name es obligatorio para emitir un evento.")
        return await self._trigger_signal_jobs(
            trigger_type="event",
            trigger_value=normalized_event,
            payload=payload or {},
            trigger_source=f"event:{normalized_event}",
            wait_for_completion=True,
        )

    async def emit_heartbeat(
        self,
        heartbeat_key: str = DEFAULT_HEARTBEAT_KEY,
        payload: dict[str, Any] | None = None,
        *,
        internal: bool = False,
    ) -> dict[str, Any]:
        normalized_key = _normalize_signal_name(
            heartbeat_key,
            default=DEFAULT_HEARTBEAT_KEY,
        )
        return await self._trigger_signal_jobs(
            trigger_type="heartbeat",
            trigger_value=normalized_key,
            payload=payload or {},
            trigger_source=f"heartbeat:{normalized_key}",
            wait_for_completion=not internal,
        )

    async def trigger_webhook(
        self,
        webhook_path: str,
        payload: dict[str, Any] | None = None,
        *,
        secret: str | None = None,
    ) -> dict[str, Any]:
        normalized_path = _normalize_webhook_path(webhook_path)
        if not normalized_path:
            raise ValueError("webhook_path es obligatorio para disparar un webhook.")
        return await self._trigger_signal_jobs(
            trigger_type="webhook",
            trigger_value=normalized_path,
            payload=payload or {},
            trigger_source=f"webhook:{normalized_path}",
            provided_secret=str(secret or "").strip() or None,
            wait_for_completion=True,
        )

    async def _run_loop(self) -> None:
        try:
            while self._running:
                try:
                    due_jobs = await self._get_due_jobs()
                    for job in due_jobs:
                        if job["job_id"] in self._active_jobs:
                            continue
                        asyncio.create_task(self._execute_job(job, trigger_source="schedule"))
                    await self.emit_heartbeat(
                        DEFAULT_HEARTBEAT_KEY,
                        {"internal": True, "timestamp": _serialize_dt(_utcnow())},
                        internal=True,
                    )
                except Exception as exc:
                    logger.error(f"Scheduler loop fallo: {exc}")
                await asyncio.sleep(self._poll_interval_seconds)
        except asyncio.CancelledError:
            logger.info("Scheduler loop cancelado")
            raise

    async def _get_due_jobs(self) -> list[dict[str, Any]]:
        now_iso = _serialize_dt(_utcnow())
        jobs: list[dict[str, Any]] = []
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM scheduled_jobs
                WHERE enabled = 1
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= ?
                ORDER BY next_run_at ASC
                """,
                (now_iso,),
            ) as cursor:
                async for row in cursor:
                    jobs.append(self._row_to_job(dict(row)))
        return jobs

    async def _load_signal_jobs(
        self,
        *,
        trigger_type: str,
        trigger_value: str,
    ) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        if trigger_type not in ("event", "heartbeat", "webhook"):
            raise ValueError(f"trigger_type no soportado para signals: {trigger_type}")

        query_map = {
            "event": "SELECT * FROM scheduled_jobs WHERE enabled = 1 AND trigger_type = 'event' AND event_name = ? ORDER BY created_at ASC",
            "heartbeat": "SELECT * FROM scheduled_jobs WHERE enabled = 1 AND trigger_type = 'heartbeat' AND heartbeat_key = ? ORDER BY created_at ASC",
            "webhook": "SELECT * FROM scheduled_jobs WHERE enabled = 1 AND trigger_type = 'webhook' AND webhook_path = ? ORDER BY created_at ASC",
        }
        sql = query_map[trigger_type]
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, (trigger_value,)) as cursor:
                async for row in cursor:
                    jobs.append(self._row_to_job(dict(row)))
        return jobs

    async def _trigger_signal_jobs(
        self,
        *,
        trigger_type: str,
        trigger_value: str,
        payload: dict[str, Any],
        trigger_source: str,
        wait_for_completion: bool,
        provided_secret: str | None = None,
    ) -> dict[str, Any]:
        matched_jobs = await self._load_signal_jobs(
            trigger_type=trigger_type,
            trigger_value=trigger_value,
        )
        if not matched_jobs:
            return {
                "trigger_type": trigger_type,
                "trigger_value": trigger_value,
                "matched_jobs": 0,
                "executed_jobs": 0,
                "queued_jobs": 0,
                "skipped_jobs": 0,
                "rejected_jobs": 0,
                "runs": [],
            }

        now = _utcnow()
        now_iso = _serialize_dt(now)
        executable_jobs: list[dict[str, Any]] = []
        skipped_jobs = 0
        rejected_jobs = 0
        secret_protected_jobs = 0
        secret_authorized_jobs = 0

        for job in matched_jobs:
            if trigger_type == "webhook":
                expected_secret = str(job.get("webhook_secret", "") or "").strip()
                if expected_secret:
                    secret_protected_jobs += 1
                    if (provided_secret or "") != expected_secret:
                        rejected_jobs += 1
                        continue
                    secret_authorized_jobs += 1

            if job["job_id"] in self._active_jobs:
                skipped_jobs += 1
                continue

            if trigger_type == "heartbeat":
                heartbeat_interval_seconds = job.get("heartbeat_interval_seconds")
                if heartbeat_interval_seconds:
                    last_signal_at = _parse_dt(job.get("last_signal_at"))
                    if (
                        last_signal_at is not None
                        and (now - last_signal_at).total_seconds()
                        < int(heartbeat_interval_seconds)
                    ):
                        skipped_jobs += 1
                        continue

            executable_jobs.append(job)

        if (
            trigger_type == "webhook"
            and secret_protected_jobs > 0
            and secret_authorized_jobs == 0
            and not executable_jobs
        ):
            raise PermissionError("Secret invalido para webhook.")

        if executable_jobs:
            async with aiosqlite.connect(self._db_path) as db:
                for job in executable_jobs:
                    await db.execute(
                        """
                        UPDATE scheduled_jobs
                        SET last_signal_at = ?, updated_at = ?
                        WHERE job_id = ?
                        """,
                        (
                            now_iso,
                            now_iso,
                            job["job_id"],
                        ),
                    )
                await db.commit()

        signal_context = {
            "type": trigger_type,
            "value": trigger_value,
            "source": trigger_source,
            "timestamp": now_iso,
            "payload": payload or {},
        }
        runs: list[dict[str, Any]] = []
        tasks: list[asyncio.Task] = []
        for job in executable_jobs:
            if wait_for_completion:
                runs.append(
                    await self._execute_job(
                        job,
                        trigger_source=trigger_source,
                        signal_context=signal_context,
                    )
                )
            else:
                tasks.append(
                    asyncio.create_task(
                        self._execute_job(
                            job,
                            trigger_source=trigger_source,
                            signal_context=signal_context,
                        )
                    )
                )

        return {
            "trigger_type": trigger_type,
            "trigger_value": trigger_value,
            "matched_jobs": len(matched_jobs),
            "executed_jobs": len(runs),
            "queued_jobs": len(tasks),
            "skipped_jobs": skipped_jobs,
            "rejected_jobs": rejected_jobs,
            "runs": runs,
        }

    async def _execute_job(
        self,
        job: dict[str, Any],
        trigger_source: str,
        signal_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job_id = job["job_id"]
        if job_id in self._active_jobs:
            raise RuntimeError(f"El job {job_id} ya esta en ejecucion.")

        self._active_jobs.add(job_id)
        try:
            run_id = f"run_{uuid.uuid4().hex[:12]}"
            started_at = _utcnow()
            started_iso = _serialize_dt(started_at)
            await self._insert_run_placeholder(run_id, job_id, trigger_source, started_iso)
            await self._record_checkpoint(
                job_id=job_id,
                run_id=run_id,
                checkpoint_type="starting",
                status="running",
                progress=5.0,
                message=f"Job iniciado desde {trigger_source}",
                payload={
                    "trigger_source": trigger_source,
                    "signal_context": signal_context,
                },
            )

            try:
                await self._record_checkpoint(
                    job_id=job_id,
                    run_id=run_id,
                    checkpoint_type="dispatching",
                    status="running",
                    progress=25.0,
                    message=f"Despachando tarea {job['task_type']}",
                    payload={"task_type": job["task_type"]},
                )
                result = await self._dispatch_job(job, signal_context=signal_context)
                status = "success" if result.get("success", True) else "error"
                error = result.get("error") if status != "success" else ""
            except Exception as exc:
                result = {"success": False, "error": str(exc)}
                status = "error"
                error = str(exc)

            finished_at = _utcnow()
            duration_ms = int((finished_at - started_at).total_seconds() * 1000)
            regular_next_run = self._compute_next_run(
                trigger_type=job["trigger_type"],
                interval_seconds=job.get("interval_seconds"),
                cron_expression=job.get("cron_expression"),
                heartbeat_interval_seconds=job.get("heartbeat_interval_seconds"),
                from_dt=finished_at,
            ) if job.get("enabled") else None
            current_retry_attempt = int(job.get("retry_attempt") or 0)
            max_retries = int(job.get("max_retries") or 0)
            next_retry_attempt = 0
            retry_delay_seconds = 0
            retry_scheduled = False
            next_run = regular_next_run

            if status == "success":
                result["retry_scheduled"] = False
                result["retry_attempt"] = 0
                result["next_run_at"] = _serialize_dt(next_run)
            elif job.get("enabled") and current_retry_attempt < max_retries:
                next_retry_attempt = current_retry_attempt + 1
                retry_delay_seconds = self._compute_retry_delay_seconds(
                    retry_backoff_seconds=job.get("retry_backoff_seconds"),
                    retry_backoff_multiplier=job.get("retry_backoff_multiplier"),
                    retry_attempt=next_retry_attempt,
                )
                next_run = finished_at + timedelta(seconds=retry_delay_seconds)
                retry_scheduled = True
                result["retry_scheduled"] = True
                result["retry_attempt"] = next_retry_attempt
                result["retry_delay_seconds"] = retry_delay_seconds
                result["next_run_at"] = _serialize_dt(next_run)
            else:
                result["retry_scheduled"] = False
                result["retry_attempt"] = 0
                result["next_run_at"] = _serialize_dt(next_run)

            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    """
                    UPDATE scheduled_runs
                    SET status = ?, finished_at = ?, duration_ms = ?, result_json = ?, error = ?
                    WHERE run_id = ?
                    """,
                    (
                        status,
                        _serialize_dt(finished_at),
                        duration_ms,
                        json.dumps(result, ensure_ascii=False),
                        error or "",
                        run_id,
                    ),
                )
                await db.execute(
                    """
                    UPDATE scheduled_jobs
                    SET next_run_at = ?, last_run_at = ?, last_error = ?, retry_attempt = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (
                        _serialize_dt(next_run),
                        _serialize_dt(finished_at),
                        error or "",
                        next_retry_attempt,
                        _serialize_dt(finished_at),
                        job_id,
                    ),
                )
                await self._record_checkpoint(
                    job_id=job_id,
                    run_id=run_id,
                    checkpoint_type="completed" if status == "success" else "error",
                    status=status,
                    progress=100.0,
                    message=(
                        f"Job completado con estado {status}"
                        if not retry_scheduled
                        else (
                            f"Job fallo y se programo retry #{next_retry_attempt} "
                            f"en {retry_delay_seconds}s"
                        )
                    ),
                    payload={
                        "result": result,
                        "retry_scheduled": retry_scheduled,
                        "retry_attempt": next_retry_attempt,
                        "retry_delay_seconds": retry_delay_seconds,
                        "next_run_at": _serialize_dt(next_run),
                    },
                    db=db,
                )
                await db.commit()

            runs = await self.list_runs(job_id=job_id, limit=1)
            latest_run = runs[0] if runs else {
                "run_id": run_id,
                "job_id": job_id,
                "status": status,
                "result": result,
                "error": error,
            }
            try:
                await get_gateway().notify_scheduler_run(job=job, run=latest_run)
            except Exception as exc:
                logger.warning(f"Gateway notify scheduler fallo para {job_id}: {exc}")
            return latest_run
        finally:
            self._active_jobs.discard(job_id)

    async def _insert_run_placeholder(self, run_id: str, job_id: str, trigger_source: str, started_iso: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO scheduled_runs (
                    run_id, job_id, trigger_source, status, started_at, finished_at,
                    duration_ms, result_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    job_id,
                    trigger_source,
                    "running",
                    started_iso,
                    None,
                    0,
                    "{}",
                    "",
                ),
            )
            await db.commit()

    async def _record_checkpoint(
        self,
        *,
        job_id: str,
        run_id: str | None,
        checkpoint_type: str,
        status: str,
        progress: float = 0.0,
        message: str = "",
        payload: Any | None = None,
        db: aiosqlite.Connection | None = None,
    ) -> None:
        checkpoint_id = f"chk_{uuid.uuid4().hex[:12]}"
        timestamp = _serialize_dt(_utcnow())
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        if db is not None:
            await db.execute(
                """
                INSERT INTO scheduled_checkpoints (
                    checkpoint_id, job_id, run_id, checkpoint_type, status,
                    progress, message, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id,
                    job_id,
                    run_id,
                    checkpoint_type,
                    status,
                    float(progress or 0.0),
                    str(message or ""),
                    payload_json,
                    timestamp,
                    timestamp,
                ),
            )
            return

        async with aiosqlite.connect(self._db_path) as local_db:
            await local_db.execute(
                """
                INSERT INTO scheduled_checkpoints (
                    checkpoint_id, job_id, run_id, checkpoint_type, status,
                    progress, message, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id,
                    job_id,
                    run_id,
                    checkpoint_type,
                    status,
                    float(progress or 0.0),
                    str(message or ""),
                    payload_json,
                    timestamp,
                    timestamp,
                ),
            )
            await local_db.commit()

    async def _recover_interrupted_runs(
        self,
        db: aiosqlite.Connection,
    ) -> dict[str, Any]:
        db.row_factory = aiosqlite.Row
        checked_at = _serialize_dt(_utcnow())
        summary: dict[str, Any] = {
            "checked_at": checked_at,
            "interrupted_runs": 0,
            "rescheduled_jobs": 0,
            "retry_scheduled_jobs": 0,
            "recovered_run_ids": [],
        }
        interrupted_rows: list[dict[str, Any]] = []
        async with db.execute(
            """
            SELECT
                r.run_id,
                r.job_id,
                r.trigger_source,
                r.started_at,
                j.trigger_type,
                j.interval_seconds,
                j.cron_expression,
                j.heartbeat_interval_seconds,
                j.enabled,
                j.max_retries,
                j.retry_backoff_seconds,
                j.retry_backoff_multiplier,
                j.retry_attempt
            FROM scheduled_runs r
            JOIN scheduled_jobs j ON j.job_id = r.job_id
            WHERE r.status = 'running'
            ORDER BY r.started_at ASC
            """
        ) as cursor:
            async for row in cursor:
                interrupted_rows.append(dict(row))

        if not interrupted_rows:
            return summary

        recovered_at = _utcnow()
        recovered_iso = _serialize_dt(recovered_at)
        for row in interrupted_rows:
            started_at = _parse_dt(row.get("started_at"))
            duration_ms = 0
            if started_at is not None:
                duration_ms = max(
                    0,
                    int((recovered_at - started_at).total_seconds() * 1000),
                )

            regular_next_run = self._compute_next_run(
                trigger_type=row["trigger_type"],
                interval_seconds=row.get("interval_seconds"),
                cron_expression=row.get("cron_expression"),
                heartbeat_interval_seconds=row.get("heartbeat_interval_seconds"),
                from_dt=recovered_at,
            ) if bool(row.get("enabled")) else None

            current_retry_attempt = int(row.get("retry_attempt") or 0)
            max_retries = int(row.get("max_retries") or 0)
            retry_scheduled = False
            next_retry_attempt = 0
            retry_delay_seconds = 0
            next_run = regular_next_run
            if bool(row.get("enabled")) and current_retry_attempt < max_retries:
                next_retry_attempt = current_retry_attempt + 1
                retry_delay_seconds = self._compute_retry_delay_seconds(
                    retry_backoff_seconds=row.get("retry_backoff_seconds"),
                    retry_backoff_multiplier=row.get("retry_backoff_multiplier"),
                    retry_attempt=next_retry_attempt,
                )
                next_run = recovered_at + timedelta(seconds=retry_delay_seconds)
                retry_scheduled = True

            recovery_result = {
                "success": False,
                "interrupted": True,
                "recovered_at": recovered_iso,
                "recovery_reason": "restart_or_crash",
                "retry_scheduled": retry_scheduled,
                "retry_attempt": next_retry_attempt,
                "retry_delay_seconds": retry_delay_seconds,
                "next_run_at": _serialize_dt(next_run),
            }

            await db.execute(
                """
                UPDATE scheduled_runs
                SET status = ?, finished_at = ?, duration_ms = ?, result_json = ?, error = ?
                WHERE run_id = ?
                """,
                (
                    "interrupted",
                    recovered_iso,
                    duration_ms,
                    json.dumps(recovery_result, ensure_ascii=False),
                    RECOVERY_ERROR_MESSAGE,
                    row["run_id"],
                ),
            )
            await db.execute(
                """
                UPDATE scheduled_jobs
                SET next_run_at = ?, last_run_at = ?, last_error = ?, retry_attempt = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    _serialize_dt(next_run),
                    recovered_iso,
                    RECOVERY_ERROR_MESSAGE,
                    next_retry_attempt,
                    recovered_iso,
                    row["job_id"],
                ),
            )
            await self._record_checkpoint(
                job_id=row["job_id"],
                run_id=row["run_id"],
                checkpoint_type="recovery",
                status="interrupted",
                progress=100.0,
                message=(
                    RECOVERY_ERROR_MESSAGE
                    if not retry_scheduled
                    else (
                        f"{RECOVERY_ERROR_MESSAGE} Reintento #{next_retry_attempt} "
                        f"programado en {retry_delay_seconds}s."
                    )
                ),
                payload={
                    "trigger_source": row.get("trigger_source"),
                    "retry_scheduled": retry_scheduled,
                    "retry_attempt": next_retry_attempt,
                    "retry_delay_seconds": retry_delay_seconds,
                    "next_run_at": _serialize_dt(next_run),
                },
                db=db,
            )
            summary["interrupted_runs"] += 1
            summary["recovered_run_ids"].append(row["run_id"])
            if next_run is not None:
                summary["rescheduled_jobs"] += 1
            if retry_scheduled:
                summary["retry_scheduled_jobs"] += 1

        logger.warning(
            "SchedulerService recupero runs interrumpidos: "
            f"{summary['interrupted_runs']} | reprogramados={summary['rescheduled_jobs']}"
        )
        return summary

    async def _dispatch_job(
        self,
        job: dict[str, Any],
        signal_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = job["payload"]
        if job["task_type"] == "skill":
            tool_input = dict(payload.get("input", {}) or {})
            if signal_context is not None:
                tool_input["_trigger"] = signal_context
            return await asyncio.to_thread(
                self._skill_runtime.run_tool,
                payload["skill_id"],
                payload["tool"],
                tool_input,
                payload.get("timeout_seconds"),
            )
        if job["task_type"] == "mcp_tool":
            tool_arguments = dict(payload.get("arguments", {}) or {})
            if signal_context is not None:
                tool_arguments["_trigger"] = signal_context
            return await asyncio.to_thread(
                self._mcp_runtime.call_tool,
                payload["server_id"],
                payload["tool"],
                tool_arguments,
                payload.get("timeout_seconds"),
            )
        if job["task_type"] == "budget_weekly_report":
            from backend.core.cost_tracker import get_cost_tracker
            tracker = get_cost_tracker()
            return await tracker.send_weekly_report_to_gateway(
                session_id=payload.get("session_id"),
                current_mode=str(payload.get("mode", "")).strip(),
            )
        raise RuntimeError(f"Task type no soportado por el scheduler: {job['task_type']}")

    async def _ensure_job_columns(self, db: aiosqlite.Connection) -> None:
        existing_columns: set[str] = set()
        async with db.execute("PRAGMA table_info(scheduled_jobs)") as cursor:
            async for row in cursor:
                existing_columns.add(str(row[1]))

        desired_columns = {
            "max_retries": "ALTER TABLE scheduled_jobs ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 0",
            "retry_backoff_seconds": (
                "ALTER TABLE scheduled_jobs ADD COLUMN retry_backoff_seconds INTEGER NOT NULL DEFAULT 30"
            ),
            "retry_backoff_multiplier": (
                "ALTER TABLE scheduled_jobs ADD COLUMN retry_backoff_multiplier REAL NOT NULL DEFAULT 2.0"
            ),
            "retry_attempt": "ALTER TABLE scheduled_jobs ADD COLUMN retry_attempt INTEGER NOT NULL DEFAULT 0",
            "event_name": "ALTER TABLE scheduled_jobs ADD COLUMN event_name TEXT DEFAULT ''",
            "webhook_path": "ALTER TABLE scheduled_jobs ADD COLUMN webhook_path TEXT DEFAULT ''",
            "webhook_secret": "ALTER TABLE scheduled_jobs ADD COLUMN webhook_secret TEXT DEFAULT ''",
            "heartbeat_key": (
                "ALTER TABLE scheduled_jobs ADD COLUMN heartbeat_key TEXT DEFAULT 'system'"
            ),
            "heartbeat_interval_seconds": (
                "ALTER TABLE scheduled_jobs ADD COLUMN heartbeat_interval_seconds INTEGER"
            ),
            "last_signal_at": "ALTER TABLE scheduled_jobs ADD COLUMN last_signal_at TEXT",
        }
        for column_name, ddl in desired_columns.items():
            if column_name not in existing_columns:
                await db.execute(ddl)

    def _normalize_trigger_config(
        self,
        *,
        trigger_type: str,
        event_name: Any,
        webhook_path: Any,
        webhook_secret: Any,
        heartbeat_key: Any,
        heartbeat_interval_seconds: Any,
    ) -> dict[str, Any]:
        normalized_trigger = str(trigger_type or "").strip().lower()
        normalized = {
            "event_name": "",
            "webhook_path": "",
            "webhook_secret": "",
            "heartbeat_key": DEFAULT_HEARTBEAT_KEY,
            "heartbeat_interval_seconds": None,
        }
        if normalized_trigger == "event":
            normalized["event_name"] = _normalize_signal_name(event_name)
        elif normalized_trigger == "webhook":
            normalized["webhook_path"] = _normalize_webhook_path(webhook_path)
            normalized["webhook_secret"] = str(webhook_secret or "").strip()
        elif normalized_trigger == "heartbeat":
            normalized["heartbeat_key"] = _normalize_signal_name(
                heartbeat_key,
                default=DEFAULT_HEARTBEAT_KEY,
            )
            if heartbeat_interval_seconds not in (None, ""):
                normalized["heartbeat_interval_seconds"] = int(heartbeat_interval_seconds)
        return normalized

    def _validate_job_definition(
        self,
        task_type: str,
        payload: dict[str, Any],
        trigger_type: str,
        interval_seconds: int | None,
        cron_expression: str | None,
        event_name: str,
        webhook_path: str,
        heartbeat_key: str,
        heartbeat_interval_seconds: int | None,
    ) -> None:
        if task_type not in {"skill", "mcp_tool", "budget_weekly_report"}:
            raise ValueError("task_type debe ser 'skill', 'mcp_tool' o 'budget_weekly_report'.")
        if not isinstance(payload, dict):
            raise ValueError("payload debe ser un objeto JSON.")
        if trigger_type not in {"interval", "cron", "heartbeat", "event", "webhook"}:
            raise ValueError(
                "trigger_type debe ser 'interval', 'cron', 'heartbeat', 'event' o 'webhook'."
            )

        if task_type == "skill":
            if not str(payload.get("skill_id", "")).strip() or not str(payload.get("tool", "")).strip():
                raise ValueError("Los jobs skill requieren payload.skill_id y payload.tool.")
        elif task_type == "mcp_tool":
            if not str(payload.get("server_id", "")).strip() or not str(payload.get("tool", "")).strip():
                raise ValueError("Los jobs mcp_tool requieren payload.server_id y payload.tool.")
        elif task_type == "budget_weekly_report":
            pass  # No payload requirements

        if trigger_type == "interval":
            try:
                parsed = int(interval_seconds or 0)
            except (TypeError, ValueError):
                parsed = 0
            if parsed < 5:
                raise ValueError("interval_seconds debe ser un entero >= 5.")
        elif trigger_type == "cron":
            expression = str(cron_expression or "").strip()
            if not expression:
                raise ValueError("cron_expression es obligatorio para trigger cron.")
            if croniter is None:
                raise RuntimeError("croniter no esta disponible. Instala dependencias del scheduler para usar cron.")
            try:
                croniter(expression, _utcnow())
            except Exception as exc:
                raise ValueError(f"cron_expression invalido: {exc}") from exc
        elif trigger_type == "heartbeat":
            if not str(heartbeat_key or "").strip():
                raise ValueError("heartbeat_key es obligatorio para trigger heartbeat.")
            if heartbeat_interval_seconds not in (None, ""):
                try:
                    parsed = int(heartbeat_interval_seconds)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "heartbeat_interval_seconds debe ser un entero >= 1."
                    ) from exc
                if parsed < 1:
                    raise ValueError(
                        "heartbeat_interval_seconds debe ser un entero >= 1."
                    )
        elif trigger_type == "event":
            if not str(event_name or "").strip():
                raise ValueError("event_name es obligatorio para trigger event.")
        elif trigger_type == "webhook":
            if not str(webhook_path or "").strip():
                raise ValueError("webhook_path es obligatorio para trigger webhook.")

    def _validate_retry_policy(
        self,
        *,
        max_retries: int | None,
        retry_backoff_seconds: int | None,
        retry_backoff_multiplier: float | None,
    ) -> dict[str, Any]:
        try:
            parsed_max_retries = int(
                DEFAULT_MAX_RETRIES if max_retries is None else max_retries
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("max_retries debe ser un entero >= 0.") from exc
        if parsed_max_retries < 0:
            raise ValueError("max_retries debe ser un entero >= 0.")

        try:
            parsed_retry_backoff_seconds = int(
                DEFAULT_RETRY_BACKOFF_SECONDS
                if retry_backoff_seconds is None
                else retry_backoff_seconds
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "retry_backoff_seconds debe ser un entero >= 1."
            ) from exc
        if parsed_retry_backoff_seconds < 1:
            raise ValueError("retry_backoff_seconds debe ser un entero >= 1.")

        try:
            parsed_retry_backoff_multiplier = float(
                DEFAULT_RETRY_BACKOFF_MULTIPLIER
                if retry_backoff_multiplier is None
                else retry_backoff_multiplier
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "retry_backoff_multiplier debe ser un numero >= 1.0."
            ) from exc
        if parsed_retry_backoff_multiplier < 1.0:
            raise ValueError("retry_backoff_multiplier debe ser un numero >= 1.0.")

        return {
            "max_retries": parsed_max_retries,
            "retry_backoff_seconds": parsed_retry_backoff_seconds,
            "retry_backoff_multiplier": parsed_retry_backoff_multiplier,
        }

    def _compute_next_run(
        self,
        *,
        trigger_type: str,
        interval_seconds: int | None,
        cron_expression: str | None,
        heartbeat_interval_seconds: int | None,
        from_dt: datetime,
    ) -> datetime | None:
        if trigger_type == "interval":
            return from_dt + timedelta(seconds=int(interval_seconds or 0))
        if trigger_type == "cron":
            if croniter is None:
                raise RuntimeError("croniter no esta disponible para calcular la siguiente ejecucion.")
            iterator = croniter(str(cron_expression), from_dt)
            return iterator.get_next(datetime)
        if trigger_type in {"heartbeat", "event", "webhook"}:
            return None
        return None

    def _compute_retry_delay_seconds(
        self,
        *,
        retry_backoff_seconds: int | None,
        retry_backoff_multiplier: float | None,
        retry_attempt: int,
    ) -> int:
        base_delay = max(1, int(retry_backoff_seconds or DEFAULT_RETRY_BACKOFF_SECONDS))
        multiplier = max(
            1.0,
            float(retry_backoff_multiplier or DEFAULT_RETRY_BACKOFF_MULTIPLIER),
        )
        exponent = max(0, int(retry_attempt) - 1)
        return max(1, int(round(base_delay * (multiplier**exponent))))

    @staticmethod
    def _row_to_job(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": row["job_id"],
            "name": row["name"],
            "task_type": row["task_type"],
            "payload": _safe_json_loads(row.get("payload_json"), {}),
            "trigger_type": row["trigger_type"],
            "interval_seconds": row["interval_seconds"],
            "cron_expression": row["cron_expression"],
            "event_name": row.get("event_name") or "",
            "webhook_path": row.get("webhook_path") or "",
            "webhook_secret": row.get("webhook_secret") or "",
            "heartbeat_key": row.get("heartbeat_key") or DEFAULT_HEARTBEAT_KEY,
            "heartbeat_interval_seconds": row.get("heartbeat_interval_seconds"),
            "enabled": bool(row["enabled"]),
            "max_retries": int(row.get("max_retries") or 0),
            "retry_backoff_seconds": int(row.get("retry_backoff_seconds") or DEFAULT_RETRY_BACKOFF_SECONDS),
            "retry_backoff_multiplier": float(row.get("retry_backoff_multiplier") or DEFAULT_RETRY_BACKOFF_MULTIPLIER),
            "retry_attempt": int(row.get("retry_attempt") or 0),
            "next_run_at": row["next_run_at"],
            "last_signal_at": row.get("last_signal_at"),
            "last_run_at": row["last_run_at"],
            "last_error": row["last_error"] or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_run(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "job_id": row["job_id"],
            "trigger_source": row["trigger_source"],
            "status": row["status"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "duration_ms": row["duration_ms"],
            "result": _safe_json_loads(row.get("result_json"), {}),
            "error": row["error"] or "",
        }

    @staticmethod
    def _row_to_checkpoint(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "checkpoint_id": row["checkpoint_id"],
            "job_id": row["job_id"],
            "run_id": row.get("run_id"),
            "checkpoint_type": row["checkpoint_type"],
            "status": row["status"],
            "progress": float(row.get("progress") or 0.0),
            "message": row.get("message") or "",
            "payload": _safe_json_loads(row.get("payload_json"), {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


_scheduler: SchedulerService | None = None


def get_scheduler() -> SchedulerService:
    global _scheduler
    if _scheduler is None:
        _scheduler = SchedulerService()
    return _scheduler
