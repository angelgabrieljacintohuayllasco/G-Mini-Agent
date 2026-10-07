"""
ETL Pipeline Engine — Extract, Transform, Load pipelines
defined in code or YAML. Supports multi-source joins.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from backend.config import config
from backend.core.workspace_manager import _SYSTEM_DENY_PATTERNS_WRITE, credential_read_reason

_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_READ_ONLY_SQL_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def _etl_path(raw: str, *, write: bool) -> Path:
    """Valida rutas de origen/destino: nada de credenciales ni carpetas del sistema."""
    path = Path(str(raw or "")).expanduser().resolve()
    reason = credential_read_reason(path)
    if reason:
        raise PermissionError(f"ETL: ruta no permitida ({reason}): {path}")
    if write:
        text = str(path).lower()
        if any(text.startswith(deny.lower()) for deny in _SYSTEM_DENY_PATTERNS_WRITE):
            raise PermissionError(f"ETL: no se escribe en carpetas del sistema: {path}")
    return path


def _sql_identifier(name: str) -> str:
    if not _SQL_IDENTIFIER_RE.match(str(name or "")):
        raise ValueError(f"ETL: identificador SQL inválido: {name!r}")
    return f'"{name}"'


@dataclass
class PipelineStep:
    step_id: str
    step_type: str          # extract | transform | load
    source_type: str        # csv, json, sqlite, api, sheets, etc.
    config: dict[str, Any]
    status: str = "pending"
    error: str | None = None
    rows_processed: int = 0

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "step_type": self.step_type,
            "source_type": self.source_type,
            "config": self.config,
            "status": self.status,
            "error": self.error,
            "rows_processed": self.rows_processed,
        }


@dataclass
class Pipeline:
    pipeline_id: str
    name: str
    description: str
    steps: list[PipelineStep]
    status: str = "pending"
    created_at: float = 0.0
    last_run: float | None = None
    schedule: str | None = None    # cron expression

    def to_dict(self) -> dict:
        return {
            "pipeline_id": self.pipeline_id,
            "name": self.name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "status": self.status,
            "created_at": self.created_at,
            "last_run": self.last_run,
            "schedule": self.schedule,
        }


class ETLEngine:
    """Manages ETL pipelines with extraction, transformation, and loading."""

    def __init__(self) -> None:
        db_path = config.get("etl", "db_path") or "data/etl.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pipelines (
                    pipeline_id  TEXT PRIMARY KEY,
                    name         TEXT NOT NULL,
                    description  TEXT NOT NULL DEFAULT '',
                    steps_json   TEXT NOT NULL DEFAULT '[]',
                    status       TEXT NOT NULL DEFAULT 'pending',
                    created_at   REAL NOT NULL,
                    last_run     REAL,
                    schedule     TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id       TEXT PRIMARY KEY,
                    pipeline_id  TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'running',
                    started_at   REAL NOT NULL,
                    finished_at  REAL,
                    rows_total   INTEGER NOT NULL DEFAULT 0,
                    error        TEXT,
                    FOREIGN KEY (pipeline_id) REFERENCES pipelines(pipeline_id)
                )
            """)
            conn.commit()

    # ── Pipeline CRUD ────────────────────────────────────────────────

    def create_pipeline(
        self,
        name: str,
        steps: list[dict],
        description: str = "",
        schedule: str | None = None,
    ) -> Pipeline:
        pipeline_id = str(uuid.uuid4())[:12]
        now = time.time()

        step_objs = []
        for s in steps:
            step_objs.append(PipelineStep(
                step_id=s.get("step_id", str(uuid.uuid4())[:8]),
                step_type=s["step_type"],
                source_type=s.get("source_type", ""),
                config=s.get("config", {}),
            ))

        pipeline = Pipeline(
            pipeline_id=pipeline_id, name=name, description=description,
            steps=step_objs, created_at=now, schedule=schedule,
        )
        self._save_pipeline(pipeline)
        return pipeline

    def get_pipeline(self, pipeline_id: str) -> Pipeline | None:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM pipelines WHERE pipeline_id = ?", (pipeline_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_pipeline(row)

    def list_pipelines(self, limit: int = 50) -> list[dict]:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM pipelines ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_pipeline(r).to_dict() for r in rows]

    def delete_pipeline(self, pipeline_id: str) -> bool:
        with sqlite3.connect(str(self._db_path)) as conn:
            c = conn.execute("DELETE FROM pipelines WHERE pipeline_id = ?", (pipeline_id,)).rowcount
            conn.commit()
            return c > 0

    # ── Execution ────────────────────────────────────────────────────

    async def run_pipeline(self, pipeline_id: str) -> dict:
        pipeline = self.get_pipeline(pipeline_id)
        if not pipeline:
            return {"ok": False, "error": "Pipeline not found"}

        run_id = str(uuid.uuid4())[:12]
        now = time.time()
        total_rows = 0
        data: list[dict] = []

        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                "INSERT INTO pipeline_runs (run_id, pipeline_id, status, started_at) VALUES (?, ?, 'running', ?)",
                (run_id, pipeline_id, now),
            )
            conn.commit()

        try:
            for step in pipeline.steps:
                step.status = "running"
                try:
                    if step.step_type == "extract":
                        data = self._extract(step)
                    elif step.step_type == "transform":
                        data = self._transform(step, data)
                    elif step.step_type == "load":
                        self._load(step, data)

                    step.rows_processed = len(data)
                    total_rows += len(data)
                    step.status = "completed"
                except Exception as e:
                    step.status = "failed"
                    step.error = str(e)
                    raise

            pipeline.status = "completed"
            pipeline.last_run = time.time()
            self._save_pipeline(pipeline)

            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute(
                    "UPDATE pipeline_runs SET status = 'completed', finished_at = ?, rows_total = ? WHERE run_id = ?",
                    (time.time(), total_rows, run_id),
                )
                conn.commit()

            return {"ok": True, "run_id": run_id, "rows_total": total_rows}

        except Exception as e:
            pipeline.status = "failed"
            self._save_pipeline(pipeline)
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute(
                    "UPDATE pipeline_runs SET status = 'failed', finished_at = ?, error = ? WHERE run_id = ?",
                    (time.time(), str(e), run_id),
                )
                conn.commit()
            return {"ok": False, "error": str(e), "run_id": run_id}

    def get_runs(self, pipeline_id: str, limit: int = 20) -> list[dict]:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM pipeline_runs WHERE pipeline_id = ? ORDER BY started_at DESC LIMIT ?",
                (pipeline_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Extract implementations ──────────────────────────────────────

    def _extract(self, step: PipelineStep) -> list[dict]:
        src = step.source_type
        cfg = step.config

        if src == "csv":
            return self._extract_csv(cfg["path"], cfg.get("delimiter", ","))
        elif src == "json":
            return self._extract_json(cfg["path"])
        elif src == "sqlite":
            return self._extract_sqlite(cfg["db_path"], cfg["query"])
        else:
            logger.warning(f"Unknown extract source: {src}")
            return []

    def _extract_csv(self, path: str, delimiter: str = ",") -> list[dict]:
        rows = []
        with open(_etl_path(path, write=False), "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                rows.append(dict(row))
        return rows

    def _extract_json(self, path: str) -> list[dict]:
        with open(_etl_path(path, write=False), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return [data]

    def _extract_sqlite(self, db_path: str, query: str) -> list[dict]:
        if not _READ_ONLY_SQL_RE.match(str(query or "")):
            raise ValueError("ETL: la extracción SQLite solo admite consultas SELECT/WITH")
        safe_path = _etl_path(db_path, write=False)
        uri = f"{safe_path.as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            conn.execute("PRAGMA query_only = ON")
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query).fetchall()
        return [dict(r) for r in rows]

    # ── Transform implementations ────────────────────────────────────

    def _transform(self, step: PipelineStep, data: list[dict]) -> list[dict]:
        cfg = step.config
        op = cfg.get("operation", "filter")

        if op == "filter":
            field_name = cfg["field"]
            value = cfg["value"]
            return [row for row in data if row.get(field_name) == value]
        elif op == "map":
            mapping = cfg.get("mapping", {})
            result = []
            for row in data:
                new_row = {}
                for new_key, old_key in mapping.items():
                    new_row[new_key] = row.get(old_key)
                result.append(new_row)
            return result
        elif op == "exclude_nulls":
            field_name = cfg["field"]
            return [row for row in data if row.get(field_name) is not None and row.get(field_name) != ""]
        else:
            return data

    # ── Load implementations ─────────────────────────────────────────

    def _load(self, step: PipelineStep, data: list[dict]) -> None:
        target = step.source_type
        cfg = step.config

        if target == "json":
            with open(_etl_path(cfg["path"], write=True), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        elif target == "csv":
            if not data:
                return
            with open(_etl_path(cfg["path"], write=True), "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=data[0].keys())
                writer.writeheader()
                writer.writerows(data)
        elif target == "sqlite":
            table = _sql_identifier(cfg["table"])
            if not data:
                return
            with sqlite3.connect(_etl_path(cfg["db_path"], write=True)) as conn:
                cols = list(data[0].keys())
                placeholders = ", ".join(["?"] * len(cols))
                col_str = ", ".join(_sql_identifier(c) for c in cols)
                for row in data:
                    conn.execute(
                        f"INSERT OR REPLACE INTO {table} ({col_str}) VALUES ({placeholders})",
                        [row.get(c) for c in cols],
                    )
                conn.commit()

    # ── Helpers ──────────────────────────────────────────────────────

    def _save_pipeline(self, p: Pipeline) -> None:
        steps_json = json.dumps([s.to_dict() for s in p.steps])
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO pipelines
                (pipeline_id, name, description, steps_json, status,
                 created_at, last_run, schedule)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (p.pipeline_id, p.name, p.description, steps_json,
                 p.status, p.created_at, p.last_run, p.schedule),
            )
            conn.commit()

    def _row_to_pipeline(self, row: sqlite3.Row) -> Pipeline:
        steps_data = json.loads(row["steps_json"])
        steps = [PipelineStep(
            step_id=s["step_id"], step_type=s["step_type"],
            source_type=s.get("source_type", ""),
            config=s.get("config", {}),
            status=s.get("status", "pending"),
            error=s.get("error"),
            rows_processed=s.get("rows_processed", 0),
        ) for s in steps_data]
        return Pipeline(
            pipeline_id=row["pipeline_id"], name=row["name"],
            description=row["description"], steps=steps,
            status=row["status"], created_at=row["created_at"],
            last_run=row["last_run"], schedule=row["schedule"],
        )


# ── Singleton ────────────────────────────────────────────────────────────

_etl: ETLEngine | None = None


def get_etl() -> ETLEngine:
    global _etl
    if _etl is None:
        _etl = ETLEngine()
    return _etl
