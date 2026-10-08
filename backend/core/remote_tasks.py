"""
G-Mini Agent — Tareas en segundo plano de la API v1 (trabajador 24/7).

Una tarea es un pedido en lenguaje natural ("revisa mi correo y resume lo
urgente") que el agente ejecuta solo: ahora mismo o según un cron/intervalo
del scheduler. El resultado queda guardado y se puede avisar por el gateway
(Telegram, WhatsApp, Discord o la app).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger

from backend.config import ROOT_DIR

DB_PATH = ROOT_DIR / "data" / "remote_tasks.db"
MAX_PROMPT_CHARS = 4000
MAX_NOTIFY_TARGETS = 5
BUSY_WAIT_SECONDS = 600
TASK_PREAMBLE = (
    "[Tarea en segundo plano pedida por el usuario; nadie está mirando la pantalla. "
    "Haz lo que puedas sin pedir confirmaciones y termina con un resumen claro del resultado.]\n"
)

_initialized = False
_running: set[asyncio.Task] = set()


def _db() -> aiosqlite.Connection:
    return aiosqlite.connect(DB_PATH)


async def _ensure_db() -> None:
    global _initialized
    if _initialized:
        return
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with _db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS remote_tasks (
                task_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                prompt TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                error TEXT,
                schedule_json TEXT,
                notify_json TEXT,
                job_id TEXT,
                runs INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL
            )
        """)
        await db.commit()
    _initialized = True


def _iso(ts: float | None) -> str | None:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else None


def validate_schedule(schedule: Any) -> dict[str, Any] | None:
    if not schedule:
        return None
    if not isinstance(schedule, dict):
        raise ValueError("schedule debe ser un objeto: {cron, timezone} o {interval_seconds}.")
    if schedule.get("at"):
        raise ValueError("Programar a una hora exacta aún no está disponible; usa un cron.")
    if schedule.get("cron"):
        return {"cron": str(schedule["cron"]).strip(), "timezone": str(schedule.get("timezone") or "")}
    if schedule.get("interval_seconds") is not None:
        try:
            seconds = int(schedule["interval_seconds"])
        except (TypeError, ValueError):
            raise ValueError("interval_seconds debe ser un entero.") from None
        if seconds < 60:
            raise ValueError("El intervalo mínimo es de 60 segundos.")
        return {"interval_seconds": seconds}
    raise ValueError("schedule debe traer 'cron' o 'interval_seconds'.")


def _row_to_task(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "task_id": row["task_id"],
        "title": row["title"],
        "prompt": row["prompt"],
        "status": row["status"],
        "result": row["result"],
        "error": row["error"],
        "schedule": json.loads(row["schedule_json"] or "null"),
        "notify": json.loads(row["notify_json"] or "[]"),
        "runs": row["runs"],
        "created_at": _iso(row["created_at"]),
        "started_at": _iso(row["started_at"]),
        "finished_at": _iso(row["finished_at"]),
    }


async def create_task(*, prompt: str, title: str = "", schedule: Any = None, notify: Any = None) -> dict[str, Any]:
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("Falta 'prompt'.")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"El prompt supera {MAX_PROMPT_CHARS} caracteres.")
    targets = [str(t).strip() for t in (notify if isinstance(notify, list) else []) if str(t).strip()]
    if len(targets) > MAX_NOTIFY_TARGETS:
        raise ValueError(f"Máximo {MAX_NOTIFY_TARGETS} destinos de aviso.")
    sched = validate_schedule(schedule)
    title = " ".join(str(title or "").split())[:120] or prompt[:60]
    task_id = f"tsk_{uuid.uuid4().hex[:12]}"
    await _ensure_db()
    async with _db() as db:
        await db.execute(
            "INSERT INTO remote_tasks (task_id, title, prompt, status, schedule_json, notify_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, title, prompt, "scheduled" if sched else "queued", json.dumps(sched),
             json.dumps(targets, ensure_ascii=False), time.time()),
        )
        await db.commit()

    if sched:
        from backend.core.scheduler import get_scheduler

        trigger = {"trigger_type": "cron", "cron_expression": sched["cron"]} if "cron" in sched else {
            "trigger_type": "interval", "interval_seconds": sched["interval_seconds"]}
        try:
            job = await get_scheduler().create_job(
                name=f"Tarea: {title}"[:120], task_type="agent_prompt", payload={"task_id": task_id},
                enabled=True, timezone_name=sched.get("timezone") or None, **trigger,
            )
        except Exception:
            await _update(task_id, status="failed", error="No se pudo programar la tarea")
            raise
        await _update(task_id, job_id=str(job.get("job_id") or job.get("id") or ""))
    else:
        task = asyncio.create_task(run_task(task_id))
        _running.add(task)
        task.add_done_callback(_running.discard)
    return await get_task(task_id) or {"task_id": task_id, "status": "queued"}


async def _update(task_id: str, **fields: Any) -> None:
    if not fields:
        return
    columns = ", ".join(f"{name} = ?" for name in fields)
    async with _db() as db:
        await db.execute(f"UPDATE remote_tasks SET {columns} WHERE task_id = ?", [*fields.values(), task_id])
        await db.commit()


async def _get_row(task_id: str) -> aiosqlite.Row | None:
    await _ensure_db()
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM remote_tasks WHERE task_id = ?", (task_id,)) as cursor:
            return await cursor.fetchone()


async def get_task(task_id: str) -> dict[str, Any] | None:
    row = await _get_row(task_id)
    if row is None:
        return None
    task = _row_to_task(row)
    if row["job_id"]:
        try:
            from backend.core.scheduler import get_scheduler

            job = await get_scheduler().get_job(row["job_id"])
            task["next_run_at"] = job.get("next_run_at")
        except Exception:
            task["next_run_at"] = None
    return task


async def list_tasks(*, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    await _ensure_db()
    sql, params = "SELECT * FROM remote_tasks", []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cursor:
            return [_row_to_task(row) for row in await cursor.fetchall()]


async def cancel_task(task_id: str) -> bool:
    row = await _get_row(task_id)
    if row is None:
        return False
    if row["job_id"]:
        try:
            from backend.core.scheduler import get_scheduler

            await get_scheduler().delete_job(row["job_id"])
        except Exception as exc:
            logger.warning(f"No se pudo borrar el job de la tarea {task_id}: {exc}")
    await _update(task_id, status="cancelled", finished_at=time.time())
    return True


async def recover_after_restart() -> dict[str, int]:
    """Al arrancar: retoma lo que quedó pendiente cuando el servidor se apagó.

    - En cola (nunca empezaron): se lanzan otra vez.
    - Corriendo cuando se cortó: una tarea única se marca fallida (repetirla
      podría duplicar lo que ya hizo, como mandar un correo dos veces); una
      programada vuelve a "scheduled" y espera su próxima corrida.
    """
    await _ensure_db()
    async with _db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT task_id, status, schedule_json FROM remote_tasks WHERE status IN ('queued', 'running')"
        ) as cursor:
            rows = await cursor.fetchall()
    counts = {"requeued": 0, "interrupted": 0, "rescheduled": 0}
    for row in rows:
        recurring = bool(json.loads(row["schedule_json"] or "null"))
        if row["status"] == "running" and recurring:
            await _update(row["task_id"], status="scheduled")
            counts["rescheduled"] += 1
        elif row["status"] == "running":
            await _update(row["task_id"], status="failed", finished_at=time.time(),
                          error="Se interrumpió porque el servidor se reinició; vuelve a enviarla si hace falta.")
            counts["interrupted"] += 1
        else:
            task = asyncio.create_task(run_task(row["task_id"]))
            _running.add(task)
            task.add_done_callback(_running.discard)
            counts["requeued"] += 1
    if any(counts.values()):
        logger.info(f"Tareas remotas tras reinicio: {counts}")
    return counts


async def run_task(task_id: str) -> dict[str, Any]:
    """Ejecuta una tarea (ya o desde el scheduler) y guarda el resultado."""
    row = await _get_row(task_id)
    if row is None or row["status"] == "cancelled":
        return {"task_id": task_id, "skipped": True}
    recurring = bool(json.loads(row["schedule_json"] or "null"))
    await _update(task_id, status="running", started_at=time.time(), error=None)
    reply, error = "", None
    try:
        from backend.api.v1 import ApiError, run_chat

        result = await run_chat(TASK_PREAMBLE + row["prompt"], wait_if_busy=BUSY_WAIT_SECONDS)
        reply = result.reply.strip()
        if result.error:
            error = result.error.get("message") or "Error del agente"
    except Exception as exc:  # ApiError (ocupado) u otros
        error = getattr(exc, "message", None) or str(exc)
    finished = "scheduled" if recurring else ("failed" if error else "done")
    await _update(task_id, status=finished, result=reply or None, error=error, finished_at=time.time(),
                  runs=int(row["runs"] or 0) + 1)
    await _notify(row, reply, error)
    return {"task_id": task_id, "status": "failed" if error else "done", "reply": reply[:2000], "error": error}


async def _notify(row: aiosqlite.Row, reply: str, error: str | None) -> None:
    targets = json.loads(row["notify_json"] or "[]")
    if not targets:
        return
    from backend.core.gateway_service import get_gateway

    body = (f"Error: {error}" if error else reply)[:3500] or "Terminé sin respuesta."
    for target in targets:
        try:
            await get_gateway().notify(
                title=row["title"], body=body, target=target, level="error" if error else "info",
                source_type="remote_task", source_id=row["task_id"],
            )
        except Exception as exc:
            logger.warning(f"No se pudo avisar la tarea {row['task_id']} a {target}: {exc}")
