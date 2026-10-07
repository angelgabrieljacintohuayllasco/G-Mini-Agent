"""
G-Mini Agent - Monitor de costos y presupuesto del LLM.
Registra uso de tokens/costos por sesion y calcula alertas de presupuesto.
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

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - fallback defensivo
    ZoneInfo = None


DEFAULT_BUDGET_DB_PATH = ROOT_DIR / "data" / "costs.db"
DEFAULT_WARNING_THRESHOLD_PERCENT = 80.0


class BudgetLimitExceeded(RuntimeError):
    """Se lanza cuando el consumo supera el presupuesto operativo permitido."""


def _resolve_budget_db_path() -> Path:
    configured = str(
        config.get("budget", "db_path", default=str(DEFAULT_BUDGET_DB_PATH))
    ).strip()
    if not configured:
        return DEFAULT_BUDGET_DB_PATH
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
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _safe_json_loads(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < 0:
        return default
    return parsed


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "si", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _round_money(value: float) -> float:
    return round(float(value or 0.0), 6)


def _local_timezone():
    if ZoneInfo is not None:
        try:
            return ZoneInfo(str(config.get("app", "timezone", default="")).strip())
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo


class CostTracker:
    def __init__(self, db_path: Path | None = None):
        self._db_path = Path(db_path) if db_path else _resolve_budget_db_path()
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _ensure_event_column(
        self,
        db: aiosqlite.Connection,
        column_name: str,
        column_sql: str,
    ) -> None:
        async with db.execute("PRAGMA table_info(llm_usage_events)") as cursor:
            columns = await cursor.fetchall()
        existing = {str(item[1]).strip().lower() for item in columns}
        if column_name.strip().lower() in existing:
            return
        await db.execute(f"ALTER TABLE llm_usage_events ADD COLUMN {column_sql}")

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS llm_usage_events (
                        event_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        model TEXT NOT NULL,
                        source TEXT NOT NULL,
                        mode_key TEXT DEFAULT '',
                        worker_id TEXT DEFAULT '',
                        worker_kind TEXT DEFAULT 'agent',
                        parent_worker_id TEXT DEFAULT '',
                        parent_task_limit_usd REAL NOT NULL DEFAULT 0,
                        input_tokens INTEGER NOT NULL DEFAULT 0,
                        output_tokens INTEGER NOT NULL DEFAULT 0,
                        estimated INTEGER NOT NULL DEFAULT 0,
                        pricing_missing INTEGER NOT NULL DEFAULT 0,
                        cost_input_usd REAL NOT NULL DEFAULT 0,
                        cost_output_usd REAL NOT NULL DEFAULT 0,
                        total_cost_usd REAL NOT NULL DEFAULT 0,
                        metadata_json TEXT DEFAULT '{}',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                await self._ensure_event_column(db, "mode_key", "mode_key TEXT DEFAULT ''")
                await self._ensure_event_column(db, "worker_id", "worker_id TEXT DEFAULT ''")
                await self._ensure_event_column(db, "worker_kind", "worker_kind TEXT DEFAULT 'agent'")
                await self._ensure_event_column(db, "parent_worker_id", "parent_worker_id TEXT DEFAULT ''")
                await self._ensure_event_column(
                    db,
                    "parent_task_limit_usd",
                    "parent_task_limit_usd REAL NOT NULL DEFAULT 0",
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_llm_usage_events_session
                    ON llm_usage_events (session_id, created_at DESC)
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_llm_usage_events_worker
                    ON llm_usage_events (session_id, worker_id, created_at DESC)
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_llm_usage_events_mode
                    ON llm_usage_events (session_id, mode_key, created_at DESC)
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_llm_usage_events_created
                    ON llm_usage_events (created_at DESC)
                    """
                )
                await db.commit()
            self._initialized = True
            logger.info(f"CostTracker inicializado: {self._db_path}")

    def _pricing_table(self) -> dict[str, dict[str, Any]]:
        raw = config.get("budget", "model_pricing", default={}) or {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(key).strip().lower(): value
            for key, value in raw.items()
            if isinstance(value, dict)
        }

    def _resolve_pricing(self, provider: str, model: str) -> tuple[dict[str, Any] | None, bool]:
        provider_key = str(provider or "").strip().lower()
        model_key = str(model or "").strip().lower()
        table = self._pricing_table()

        if model_key in table:
            return table[model_key], False

        for entry_key, entry in table.items():
            aliases = entry.get("aliases", [])
            if not isinstance(aliases, list):
                continue
            normalized_aliases = {str(alias).strip().lower() for alias in aliases if str(alias).strip()}
            if model_key in normalized_aliases:
                return entry, False

        if provider_key in {"ollama", "lmstudio"}:
            return {
                "input_per_million_usd": 0.0,
                "output_per_million_usd": 0.0,
            }, False

        return None, True

    def _compute_event_cost(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> dict[str, Any]:
        pricing, pricing_missing = self._resolve_pricing(provider, model)
        input_rate = _coerce_float((pricing or {}).get("input_per_million_usd"), 0.0)
        output_rate = _coerce_float((pricing or {}).get("output_per_million_usd"), 0.0)

        input_cost = (max(0, int(input_tokens)) / 1_000_000.0) * input_rate
        output_cost = (max(0, int(output_tokens)) / 1_000_000.0) * output_rate
        total_cost = input_cost + output_cost

        return {
            "pricing_missing": bool(pricing_missing),
            "cost_input_usd": _round_money(input_cost),
            "cost_output_usd": _round_money(output_cost),
            "total_cost_usd": _round_money(total_cost),
            "pricing": pricing or {},
        }

    def _mode_limits_table(self) -> dict[str, float]:
        raw = config.get("budget", "mode_task_limits_usd", default={}) or {}
        if not isinstance(raw, dict):
            return {}
        table: dict[str, float] = {}
        for key, value in raw.items():
            normalized_key = str(key or "").strip().lower()
            if not normalized_key:
                continue
            parsed = _coerce_float(value, 0.0)
            if parsed > 0:
                table[normalized_key] = parsed
        return table

    def _resolve_task_limit_for_mode(self, current_mode: str) -> tuple[float, float]:
        base_task_limit = _coerce_float(
            config.get("model_router", "hard_limits", "max_cost_per_task_usd", default=0.0),
            0.0,
        )
        mode_limits = self._mode_limits_table()
        mode_limit = _coerce_float(mode_limits.get(str(current_mode or "").strip().lower(), 0.0), 0.0)
        effective = mode_limit if mode_limit > 0 else base_task_limit
        return effective, mode_limit

    def _resolve_subagent_limit(
        self,
        *,
        base_task_limit_usd: float,
        parent_task_limit_usd: float,
    ) -> float:
        default_limit = _coerce_float(
            config.get("budget", "subagent_default_task_limit_usd", default=0.0),
            0.0,
        )
        share = _coerce_float(
            config.get("budget", "subagent_parent_budget_share", default=0.0),
            0.0,
        )

        candidates = [
            value
            for value in (
                default_limit,
                parent_task_limit_usd * share if parent_task_limit_usd > 0 and share > 0 else 0.0,
                base_task_limit_usd,
            )
            if value > 0
        ]
        return min(candidates) if candidates else 0.0

    async def _aggregate_usage(
        self,
        *,
        session_id: str | None = None,
        mode_key: str | None = None,
        worker_id: str | None = None,
        worker_kind: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
    ) -> dict[str, Any]:
        await self.initialize()

        where_parts: list[str] = []
        params: list[Any] = []
        if session_id:
            where_parts.append("session_id = ?")
            params.append(session_id)
        if mode_key:
            where_parts.append("mode_key = ?")
            params.append(str(mode_key).strip().lower())
        if worker_id:
            where_parts.append("worker_id = ?")
            params.append(str(worker_id).strip())
        if worker_kind:
            where_parts.append("worker_kind = ?")
            params.append(str(worker_kind).strip().lower())
        if start_at:
            where_parts.append("created_at >= ?")
            params.append(start_at)
        if end_at:
            where_parts.append("created_at < ?")
            params.append(end_at)

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                f"""
                SELECT
                    COUNT(*) AS event_count,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(cost_input_usd), 0) AS input_cost_usd,
                    COALESCE(SUM(cost_output_usd), 0) AS output_cost_usd,
                    COALESCE(SUM(total_cost_usd), 0) AS total_cost_usd,
                    COALESCE(SUM(CASE WHEN estimated = 1 THEN 1 ELSE 0 END), 0) AS estimated_events,
                    COALESCE(SUM(CASE WHEN pricing_missing = 1 THEN 1 ELSE 0 END), 0) AS unpriced_events,
                    MAX(created_at) AS last_event_at
                FROM llm_usage_events
                {where_sql}
                """,
                params,
            ) as cursor:
                row = await cursor.fetchone()

        event_count = int((row or [0])[0] or 0)
        input_tokens = int((row or [0, 0])[1] or 0)
        output_tokens = int((row or [0, 0, 0])[2] or 0)
        input_cost = _round_money((row or [0, 0, 0, 0])[3] or 0.0)
        output_cost = _round_money((row or [0, 0, 0, 0, 0])[4] or 0.0)
        total_cost = _round_money((row or [0, 0, 0, 0, 0, 0])[5] or 0.0)
        estimated_events = int((row or [0, 0, 0, 0, 0, 0, 0])[6] or 0)
        unpriced_events = int((row or [0, 0, 0, 0, 0, 0, 0, 0])[7] or 0)
        last_event_at = (row or [None] * 9)[8]

        return {
            "event_count": event_count,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_cost_usd": input_cost,
            "output_cost_usd": output_cost,
            "total_cost_usd": total_cost,
            "estimated_events": estimated_events,
            "unpriced_events": unpriced_events,
            "last_event_at": last_event_at,
        }

    def _time_windows(self) -> tuple[str, str]:
        now_utc = _utcnow()
        tzinfo = _local_timezone()
        now_local = now_utc.astimezone(tzinfo) if tzinfo else now_utc
        start_of_day_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_month_local = start_of_day_local.replace(day=1)
        start_of_day_utc = start_of_day_local.astimezone(timezone.utc)
        start_of_month_utc = start_of_month_local.astimezone(timezone.utc)
        return _serialize_dt(start_of_day_utc), _serialize_dt(start_of_month_utc)

    def _weekly_report_targets(self) -> list[str]:
        raw = config.get("budget", "weekly_report_gateway_targets", default=[]) or []
        if not isinstance(raw, list):
            return []
        return [
            str(item).strip()
            for item in raw
            if str(item).strip()
        ]

    def _weekly_time_window(
        self,
        *,
        week_offset: int = 0,
        include_current_week: bool = False,
    ) -> dict[str, Any]:
        safe_offset = max(0, int(week_offset or 0))
        now_utc = _utcnow()
        tzinfo = _local_timezone()
        now_local = now_utc.astimezone(tzinfo) if tzinfo else now_utc
        start_of_today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_current_week_local = start_of_today_local - timedelta(days=start_of_today_local.weekday())

        if include_current_week:
            start_local = start_of_current_week_local - timedelta(weeks=safe_offset)
            end_local = start_local + timedelta(days=7)
            if safe_offset == 0 and end_local > now_local:
                end_local = now_local
        else:
            start_local = start_of_current_week_local - timedelta(weeks=safe_offset + 1)
            end_local = start_local + timedelta(days=7)

        previous_start_local = start_local - timedelta(days=7)
        previous_end_local = start_local
        end_display_local = (
            end_local - timedelta(microseconds=1)
            if end_local > start_local
            else end_local
        )
        previous_end_display_local = (
            previous_end_local - timedelta(microseconds=1)
            if previous_end_local > previous_start_local
            else previous_end_local
        )
        start_utc = start_local.astimezone(timezone.utc)
        end_utc = end_local.astimezone(timezone.utc)
        previous_start_utc = previous_start_local.astimezone(timezone.utc)
        previous_end_utc = previous_end_local.astimezone(timezone.utc)
        iso_year, iso_week, _ = start_local.isocalendar()
        window_label = f"{iso_year}-W{iso_week:02d}"

        return {
            "week_offset": safe_offset,
            "include_current_week": bool(include_current_week),
            "window_label": window_label,
            "week_start": _serialize_dt(start_utc),
            "week_end": _serialize_dt(end_utc),
            "previous_week_start": _serialize_dt(previous_start_utc),
            "previous_week_end": _serialize_dt(previous_end_utc),
            "week_start_local": start_local.date().isoformat(),
            "week_end_local": end_display_local.date().isoformat(),
            "previous_week_start_local": previous_start_local.date().isoformat(),
            "previous_week_end_local": previous_end_display_local.date().isoformat(),
        }

    async def _aggregate_grouped_usage(
        self,
        *,
        group_field: str,
        session_id: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        await self.initialize()

        allowed_fields = {
            "provider": "provider",
            "model": "model",
            "mode_key": "mode_key",
            "worker_kind": "worker_kind",
            "source": "source",
        }
        normalized_group = allowed_fields.get(str(group_field or "").strip().lower())
        if not normalized_group:
            raise ValueError(f"group_field no soportado: {group_field}")

        safe_limit = max(1, min(int(limit or 5), 25))
        where_parts: list[str] = []
        params: list[Any] = []
        if session_id:
            where_parts.append("session_id = ?")
            params.append(session_id)
        if start_at:
            where_parts.append("created_at >= ?")
            params.append(start_at)
        if end_at:
            where_parts.append("created_at < ?")
            params.append(end_at)
        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                f"""
                SELECT
                    COALESCE({normalized_group}, '') AS bucket_key,
                    COUNT(*) AS event_count,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(cost_input_usd), 0) AS input_cost_usd,
                    COALESCE(SUM(cost_output_usd), 0) AS output_cost_usd,
                    COALESCE(SUM(total_cost_usd), 0) AS total_cost_usd,
                    COALESCE(SUM(CASE WHEN estimated = 1 THEN 1 ELSE 0 END), 0) AS estimated_events,
                    COALESCE(SUM(CASE WHEN pricing_missing = 1 THEN 1 ELSE 0 END), 0) AS unpriced_events,
                    MAX(created_at) AS last_event_at
                FROM llm_usage_events
                {where_sql}
                GROUP BY bucket_key
                ORDER BY total_cost_usd DESC, event_count DESC, bucket_key ASC
                LIMIT ?
                """,
                [*params, safe_limit],
            ) as cursor:
                rows = await cursor.fetchall()

        grouped: list[dict[str, Any]] = []
        for row in rows:
            input_tokens = int(row[2] or 0)
            output_tokens = int(row[3] or 0)
            grouped.append(
                {
                    "key": str(row[0] or "").strip(),
                    "event_count": int(row[1] or 0),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    "input_cost_usd": _round_money(row[4] or 0.0),
                    "output_cost_usd": _round_money(row[5] or 0.0),
                    "total_cost_usd": _round_money(row[6] or 0.0),
                    "estimated_events": int(row[7] or 0),
                    "unpriced_events": int(row[8] or 0),
                    "last_event_at": row[9],
                }
            )
        return grouped

    async def _aggregate_daily_usage(
        self,
        *,
        session_id: str | None = None,
        start_at: str,
        end_at: str,
    ) -> list[dict[str, Any]]:
        await self.initialize()
        tzinfo = _local_timezone()
        start_dt = _parse_dt(start_at)
        end_dt = _parse_dt(end_at)
        if start_dt is None or end_dt is None:
            return []

        params: list[Any] = [start_at, end_at]
        where_sql = "WHERE created_at >= ? AND created_at < ?"
        if session_id:
            where_sql += " AND session_id = ?"
            params.append(session_id)

        buckets: dict[str, dict[str, Any]] = {}
        cursor_day = start_dt.astimezone(tzinfo) if tzinfo else start_dt
        end_day = end_dt.astimezone(tzinfo) if tzinfo else end_dt
        last_bucket_day = (
            (end_day - timedelta(microseconds=1)).date()
            if end_day > cursor_day
            else cursor_day.date()
        )
        while cursor_day.date() <= last_bucket_day:
            day_key = cursor_day.date().isoformat()
            buckets[day_key] = {
                "date": day_key,
                "label": cursor_day.strftime("%a %d"),
                "event_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "input_cost_usd": 0.0,
                "output_cost_usd": 0.0,
                "total_cost_usd": 0.0,
            }
            cursor_day += timedelta(days=1)

        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                f"""
                SELECT
                    created_at,
                    input_tokens,
                    output_tokens,
                    cost_input_usd,
                    cost_output_usd,
                    total_cost_usd
                FROM llm_usage_events
                {where_sql}
                ORDER BY created_at ASC
                """,
                params,
            ) as cursor:
                rows = await cursor.fetchall()

        for created_at, input_tokens, output_tokens, input_cost, output_cost, total_cost in rows:
            created_dt = _parse_dt(created_at)
            if created_dt is None:
                continue
            local_dt = created_dt.astimezone(tzinfo) if tzinfo else created_dt
            day_key = local_dt.date().isoformat()
            bucket = buckets.get(day_key)
            if not bucket:
                continue
            bucket["event_count"] += 1
            bucket["input_tokens"] += int(input_tokens or 0)
            bucket["output_tokens"] += int(output_tokens or 0)
            bucket["total_tokens"] += int(input_tokens or 0) + int(output_tokens or 0)
            bucket["input_cost_usd"] = _round_money(bucket["input_cost_usd"] + float(input_cost or 0.0))
            bucket["output_cost_usd"] = _round_money(bucket["output_cost_usd"] + float(output_cost or 0.0))
            bucket["total_cost_usd"] = _round_money(bucket["total_cost_usd"] + float(total_cost or 0.0))

        return [buckets[key] for key in sorted(buckets.keys())]

    def _evaluate_limit(
        self,
        *,
        total_cost_usd: float,
        limit_usd: float,
        warning_threshold_percent: float,
    ) -> dict[str, Any]:
        limit_value = _coerce_float(limit_usd, 0.0)
        if limit_value <= 0:
            return {
                "limit_usd": limit_value,
                "usage_percent": 0.0,
                "remaining_usd": None,
                "state": "unlimited",
                "stop_required": False,
            }

        usage_percent = round((float(total_cost_usd or 0.0) / limit_value) * 100.0, 2)
        remaining = _round_money(limit_value - float(total_cost_usd or 0.0))
        state = "ok"
        if float(total_cost_usd or 0.0) >= limit_value:
            state = "exceeded"
        elif usage_percent >= warning_threshold_percent:
            state = "warning"

        return {
            "limit_usd": limit_value,
            "usage_percent": usage_percent,
            "remaining_usd": remaining,
            "state": state,
            "stop_required": state == "exceeded",
        }

    def _build_budget_status(
        self,
        *,
        session_total_usd: float,
        daily_total_usd: float,
        monthly_total_usd: float,
        current_mode: str,
        mode_total_usd: float,
        worker_total_usd: float,
        worker_kind: str,
        worker_id: str,
        parent_task_limit_usd: float,
    ) -> dict[str, Any]:
        enabled = _coerce_bool(config.get("budget", "enabled", default=False), default=False)
        warning_threshold = _coerce_float(
            config.get("budget", "warning_threshold_percent", default=DEFAULT_WARNING_THRESHOLD_PERCENT),
            DEFAULT_WARNING_THRESHOLD_PERCENT,
        )
        daily_limit = _coerce_float(config.get("budget", "daily_limit_usd", default=0.0), 0.0)
        monthly_limit = _coerce_float(config.get("budget", "monthly_limit_usd", default=0.0), 0.0)
        task_limit, mode_limit = self._resolve_task_limit_for_mode(current_mode)
        normalized_worker_kind = str(worker_kind or "").strip().lower()
        is_subagent_worker = normalized_worker_kind in {"subagent", "critic"}
        subagent_limit = self._resolve_subagent_limit(
            base_task_limit_usd=task_limit,
            parent_task_limit_usd=_coerce_float(parent_task_limit_usd, 0.0),
        ) if is_subagent_worker else 0.0

        session_budget = self._evaluate_limit(
            total_cost_usd=session_total_usd,
            limit_usd=task_limit,
            warning_threshold_percent=warning_threshold,
        )
        daily_budget = self._evaluate_limit(
            total_cost_usd=daily_total_usd,
            limit_usd=daily_limit,
            warning_threshold_percent=warning_threshold,
        )
        monthly_budget = self._evaluate_limit(
            total_cost_usd=monthly_total_usd,
            limit_usd=monthly_limit,
            warning_threshold_percent=warning_threshold,
        )
        mode_budget = self._evaluate_limit(
            total_cost_usd=mode_total_usd,
            limit_usd=mode_limit,
            warning_threshold_percent=warning_threshold,
        )
        current_worker_budget = self._evaluate_limit(
            total_cost_usd=worker_total_usd,
            limit_usd=subagent_limit,
            warning_threshold_percent=warning_threshold,
        )

        alerts: list[str] = []
        label_map = {
            "session": "presupuesto por tarea",
            "daily": "presupuesto diario",
            "monthly": "presupuesto mensual",
            "mode": f"presupuesto del modo {current_mode or 'actual'}",
            "current_worker": f"presupuesto del worker {worker_id or 'actual'}",
        }
        for scope, bucket, total in (
            ("session", session_budget, session_total_usd),
            ("daily", daily_budget, daily_total_usd),
            ("monthly", monthly_budget, monthly_total_usd),
            ("mode", mode_budget, mode_total_usd),
            ("current_worker", current_worker_budget, worker_total_usd),
        ):
            if bucket["state"] == "unlimited":
                continue
            if bucket["state"] == "warning":
                alerts.append(
                    f"Aviso: {label_map[scope]} al {bucket['usage_percent']}% "
                    f"(${_round_money(total)} de ${bucket['limit_usd']})."
                )
            elif bucket["state"] == "exceeded":
                alerts.append(
                    f"Limite superado: {label_map[scope]} consumio "
                    f"${_round_money(total)} de ${bucket['limit_usd']}."
                )

        stop_required = enabled and any(
            bucket["stop_required"]
            for bucket in (session_budget, daily_budget, monthly_budget, mode_budget, current_worker_budget)
        )

        return {
            "enabled": enabled,
            "warning_threshold_percent": warning_threshold,
            "session": session_budget,
            "daily": daily_budget,
            "monthly": monthly_budget,
            "mode": mode_budget,
            "current_worker": current_worker_budget,
            "task_limit_usd": task_limit,
            "mode_limit_usd": mode_limit,
            "subagent_effective_limit_usd": subagent_limit,
            "daily_limit_usd": daily_limit,
            "monthly_limit_usd": monthly_limit,
            "alerts": alerts,
            "stop_required": stop_required,
        }

    async def record_llm_usage(
        self,
        *,
        session_id: str,
        provider: str,
        model: str,
        source: str,
        mode_key: str = "",
        worker_id: str = "",
        worker_kind: str = "agent",
        parent_worker_id: str = "",
        parent_task_limit_usd: float = 0.0,
        input_tokens: int,
        output_tokens: int,
        estimated: bool,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self.initialize()

        event_id = f"usage_{uuid.uuid4().hex[:12]}"
        created_at = _serialize_dt(_utcnow())
        input_tokens = max(0, int(input_tokens or 0))
        output_tokens = max(0, int(output_tokens or 0))
        cost_data = self._compute_event_cost(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        payload = {
            "event_id": event_id,
            "session_id": session_id,
            "provider": str(provider or "").strip(),
            "model": str(model or "").strip(),
            "source": str(source or "agent_loop").strip() or "agent_loop",
            "mode_key": str(mode_key or "").strip().lower(),
            "worker_id": str(worker_id or "").strip(),
            "worker_kind": str(worker_kind or "agent").strip().lower() or "agent",
            "parent_worker_id": str(parent_worker_id or "").strip(),
            "parent_task_limit_usd": _coerce_float(parent_task_limit_usd, 0.0),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "estimated": bool(estimated),
            "pricing_missing": bool(cost_data["pricing_missing"]),
            "cost_input_usd": cost_data["cost_input_usd"],
            "cost_output_usd": cost_data["cost_output_usd"],
            "total_cost_usd": cost_data["total_cost_usd"],
            "metadata": metadata or {},
            "created_at": created_at,
        }

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO llm_usage_events (
                    event_id,
                    session_id,
                        provider,
                        model,
                        source,
                        mode_key,
                        worker_id,
                        worker_kind,
                        parent_worker_id,
                        parent_task_limit_usd,
                        input_tokens,
                        output_tokens,
                        estimated,
                    pricing_missing,
                    cost_input_usd,
                    cost_output_usd,
                    total_cost_usd,
                    metadata_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["event_id"],
                    payload["session_id"],
                    payload["provider"],
                    payload["model"],
                    payload["source"],
                    payload["mode_key"],
                    payload["worker_id"],
                    payload["worker_kind"],
                    payload["parent_worker_id"],
                    payload["parent_task_limit_usd"],
                    payload["input_tokens"],
                    payload["output_tokens"],
                    1 if payload["estimated"] else 0,
                    1 if payload["pricing_missing"] else 0,
                    payload["cost_input_usd"],
                    payload["cost_output_usd"],
                    payload["total_cost_usd"],
                    json.dumps(payload["metadata"], ensure_ascii=False),
                    payload["created_at"],
                ),
            )
            await db.commit()

        summary = await self.get_summary(
            session_id=session_id,
            current_mode=payload["mode_key"],
            worker_id=payload["worker_id"] or None,
            worker_kind=payload["worker_kind"],
            parent_task_limit_usd=payload["parent_task_limit_usd"],
        )
        payload["budget_status"] = summary["budget_status"]
        return payload

    async def list_events(
        self,
        *,
        session_id: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        await self.initialize()
        safe_limit = max(1, min(int(limit or 30), 200))

        params: list[Any] = []
        where_sql = ""
        if session_id:
            where_sql = "WHERE session_id = ?"
            params.append(session_id)

        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                f"""
                SELECT
                    event_id,
                    session_id,
                    provider,
                    model,
                    source,
                    mode_key,
                    worker_id,
                    worker_kind,
                    parent_worker_id,
                    parent_task_limit_usd,
                    input_tokens,
                    output_tokens,
                    estimated,
                    pricing_missing,
                    cost_input_usd,
                    cost_output_usd,
                    total_cost_usd,
                    metadata_json,
                    created_at
                FROM llm_usage_events
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [*params, safe_limit],
            ) as cursor:
                rows = await cursor.fetchall()

        events: list[dict[str, Any]] = []
        for row in rows:
            input_tokens = int(row[10] or 0)
            output_tokens = int(row[11] or 0)
            events.append(
                {
                    "event_id": row[0],
                    "session_id": row[1],
                    "provider": row[2],
                    "model": row[3],
                    "source": row[4],
                    "mode_key": row[5] or "",
                    "worker_id": row[6] or "",
                    "worker_kind": row[7] or "agent",
                    "parent_worker_id": row[8] or "",
                    "parent_task_limit_usd": _round_money(row[9] or 0.0),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    "estimated": bool(row[12]),
                    "pricing_missing": bool(row[13]),
                    "cost_input_usd": _round_money(row[14] or 0.0),
                    "cost_output_usd": _round_money(row[15] or 0.0),
                    "total_cost_usd": _round_money(row[16] or 0.0),
                    "metadata": _safe_json_loads(row[17], {}),
                    "created_at": row[18],
                }
            )
        return events

    async def get_summary(
        self,
        *,
        session_id: str | None = None,
        current_mode: str = "",
        worker_id: str | None = None,
        worker_kind: str = "",
        parent_task_limit_usd: float = 0.0,
    ) -> dict[str, Any]:
        await self.initialize()
        start_of_day, start_of_month = self._time_windows()
        normalized_mode = str(current_mode or "").strip().lower()
        normalized_worker_id = str(worker_id or "").strip() or None
        normalized_worker_kind = str(worker_kind or "").strip().lower()

        current_session = await self._aggregate_usage(session_id=session_id)
        today = await self._aggregate_usage(start_at=start_of_day)
        month = await self._aggregate_usage(start_at=start_of_month)
        current_mode_usage = await self._aggregate_usage(
            session_id=session_id,
            mode_key=normalized_mode or None,
        ) if normalized_mode else {
            "event_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "input_cost_usd": 0.0,
            "output_cost_usd": 0.0,
            "total_cost_usd": 0.0,
            "estimated_events": 0,
            "unpriced_events": 0,
            "last_event_at": None,
        }
        current_worker = await self._aggregate_usage(
            session_id=session_id,
            worker_id=normalized_worker_id,
            worker_kind=normalized_worker_kind or None,
        ) if normalized_worker_id else {
            "event_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "input_cost_usd": 0.0,
            "output_cost_usd": 0.0,
            "total_cost_usd": 0.0,
            "estimated_events": 0,
            "unpriced_events": 0,
            "last_event_at": None,
        }
        budget_status = self._build_budget_status(
            session_total_usd=float(current_session["total_cost_usd"]),
            daily_total_usd=float(today["total_cost_usd"]),
            monthly_total_usd=float(month["total_cost_usd"]),
            current_mode=normalized_mode,
            mode_total_usd=float(current_mode_usage["total_cost_usd"]),
            worker_total_usd=float(current_worker["total_cost_usd"]),
            worker_kind=normalized_worker_kind,
            worker_id=normalized_worker_id or "",
            parent_task_limit_usd=_coerce_float(parent_task_limit_usd, 0.0),
        )

        return {
            "enabled": _coerce_bool(config.get("budget", "enabled", default=False), default=False),
            "session_id": session_id,
            "current_mode": normalized_mode,
            "current_worker_id": normalized_worker_id,
            "current_worker_kind": normalized_worker_kind,
            "configured_models": len(self._pricing_table()),
            "current_session": current_session,
            "current_mode_usage": current_mode_usage,
            "current_worker": current_worker,
            "today": today,
            "month": month,
            "budget_status": budget_status,
            "alerts": budget_status["alerts"],
            "checked_at": _serialize_dt(_utcnow()),
        }

    async def get_weekly_report(
        self,
        *,
        session_id: str | None = None,
        current_mode: str = "",
        week_offset: int = 0,
        include_current_week: bool = False,
        top_n: int = 5,
    ) -> dict[str, Any]:
        await self.initialize()

        window = self._weekly_time_window(
            week_offset=week_offset,
            include_current_week=include_current_week,
        )
        totals = await self._aggregate_usage(
            session_id=session_id,
            start_at=window["week_start"],
            end_at=window["week_end"],
        )
        previous_totals = await self._aggregate_usage(
            session_id=session_id,
            start_at=window["previous_week_start"],
            end_at=window["previous_week_end"],
        )
        provider_breakdown = await self._aggregate_grouped_usage(
            group_field="provider",
            session_id=session_id,
            start_at=window["week_start"],
            end_at=window["week_end"],
            limit=top_n,
        )
        mode_breakdown = await self._aggregate_grouped_usage(
            group_field="mode_key",
            session_id=session_id,
            start_at=window["week_start"],
            end_at=window["week_end"],
            limit=top_n,
        )
        worker_kind_breakdown = await self._aggregate_grouped_usage(
            group_field="worker_kind",
            session_id=session_id,
            start_at=window["week_start"],
            end_at=window["week_end"],
            limit=top_n,
        )
        daily_breakdown = await self._aggregate_daily_usage(
            session_id=session_id,
            start_at=window["week_start"],
            end_at=window["week_end"],
        )

        weekly_total = float(totals.get("total_cost_usd", 0.0) or 0.0)
        previous_total = float(previous_totals.get("total_cost_usd", 0.0) or 0.0)
        delta_total = _round_money(weekly_total - previous_total)
        delta_percent = None
        if previous_total > 0:
            delta_percent = round((delta_total / previous_total) * 100.0, 2)

        for bucket in provider_breakdown + mode_breakdown + worker_kind_breakdown:
            total = float(bucket.get("total_cost_usd", 0.0) or 0.0)
            bucket["label"] = bucket.get("key") or "(sin dato)"
            bucket["share_percent"] = round((total / weekly_total) * 100.0, 2) if weekly_total > 0 else 0.0

        delivery_targets = self._weekly_report_targets()
        top_provider = provider_breakdown[0] if provider_breakdown else None
        highlights: list[str] = [
            (
                f"Semana {window['window_label']}: gasto {_round_money(weekly_total):.4f} USD "
                f"en {int(totals.get('event_count', 0) or 0)} evento(s)."
            )
        ]
        if top_provider:
            highlights.append(
                f"Proveedor dominante: {top_provider['label']} con {_round_money(top_provider['total_cost_usd']):.4f} USD "
                f"({top_provider['share_percent']:.2f}% del total)."
            )
        if previous_total > 0:
            direction = "subio" if delta_total >= 0 else "bajo"
            highlights.append(
                f"Vs semana previa: {direction} {abs(delta_percent or 0.0):.2f}% "
                f"({_round_money(abs(delta_total)):.4f} USD)."
            )
        elif weekly_total > 0:
            highlights.append("No hubo gasto en la semana previa comparable.")
        if delivery_targets:
            highlights.append(
                "Targets configurados para entrega futura por gateway: "
                + ", ".join(delivery_targets)
            )
        else:
            highlights.append(
                "No hay gateway targets configurados; el reporte queda disponible en API, planner y UI."
            )

        return {
            "session_id": session_id,
            "current_mode": str(current_mode or "").strip().lower(),
            "week_offset": int(window["week_offset"]),
            "include_current_week": bool(window["include_current_week"]),
            "window_label": window["window_label"],
            "week_start": window["week_start"],
            "week_end": window["week_end"],
            "week_start_local": window["week_start_local"],
            "week_end_local": window["week_end_local"],
            "previous_week_start": window["previous_week_start"],
            "previous_week_end": window["previous_week_end"],
            "previous_week_start_local": window["previous_week_start_local"],
            "previous_week_end_local": window["previous_week_end_local"],
            "totals": totals,
            "previous_totals": previous_totals,
            "delta_total_cost_usd": delta_total,
            "delta_percent": delta_percent,
            "provider_breakdown": provider_breakdown,
            "mode_breakdown": mode_breakdown,
            "worker_kind_breakdown": worker_kind_breakdown,
            "daily_breakdown": daily_breakdown,
            "delivery_targets": delivery_targets,
            "delivery_status": "ready_for_gateway" if delivery_targets else "preview_only",
            "highlights": highlights,
            "checked_at": _serialize_dt(_utcnow()),
        }

    async def send_weekly_report_to_gateway(
        self,
        *,
        session_id: str | None = None,
        current_mode: str = "",
    ) -> dict[str, Any]:
        """Generate and send weekly cost report to configured gateway targets."""
        report = await self.get_weekly_report(
            session_id=session_id,
            current_mode=current_mode,
            include_current_week=True,
        )
        targets = report.get("delivery_targets", [])
        if not targets:
            logger.info("Weekly report: sin gateway targets configurados, saltando envío")
            return {"sent": 0, "targets": [], "report": report}

        highlights = report.get("highlights", [])
        text = "*Reporte semanal de costos*\n\n" + "\n".join(highlights)
        totals = report.get("totals", {})
        text += f"\n\nTotal: ${float(totals.get('total_cost_usd', 0)):.4f} USD"
        text += f" | Eventos: {int(totals.get('event_count', 0))}"
        text += f" | Tokens: {int(totals.get('total_tokens', 0)):,}"

        results: list[dict[str, Any]] = []
        try:
            from backend.core.gateway_service import get_gateway
            gateway = get_gateway()
        except Exception as exc:
            logger.error(f"Weekly report: no se pudo obtener GatewayService: {exc}")
            return {"sent": 0, "targets": targets, "error": str(exc), "report": report}

        for target in targets:
            parts = str(target).split(":", 1)
            if len(parts) != 2:
                logger.warning(f"Weekly report: target inválido (formato channel:session): {target}")
                results.append({"target": target, "status": "invalid_format"})
                continue
            channel, session_key = parts[0].strip(), parts[1].strip()
            try:
                await gateway.send_text_notification(
                    channel=channel,
                    session_key=session_key,
                    text=text,
                    level="info",
                    source_type="cost_tracker",
                    source_id="weekly_report",
                )
                results.append({"target": target, "status": "sent"})
                logger.info(f"Weekly report enviado a {target}")
            except Exception as exc:
                results.append({"target": target, "status": "error", "error": str(exc)})
                logger.error(f"Weekly report fallo para {target}: {exc}")

        sent_count = sum(1 for r in results if r["status"] == "sent")
        return {"sent": sent_count, "targets": results, "report": report}

    def recommend_cheaper_model(self, current_model: str) -> str | None:
        """Return a cheaper model than current_model from pricing table, or None."""
        table = self._pricing_table()
        current_key = str(current_model or "").strip().lower()
        current_pricing = table.get(current_key)
        if not current_pricing:
            return None

        current_cost = (
            float(current_pricing.get("input_per_million_usd", 0))
            + float(current_pricing.get("output_per_million_usd", 0))
        )

        cheapest_model: str | None = None
        cheapest_cost = current_cost

        for model_key, pricing in table.items():
            if model_key == current_key:
                continue
            combined = (
                float(pricing.get("input_per_million_usd", 0))
                + float(pricing.get("output_per_million_usd", 0))
            )
            if combined < cheapest_cost:
                cheapest_cost = combined
                cheapest_model = model_key

        return cheapest_model


_cost_tracker: CostTracker | None = None


def get_cost_tracker() -> CostTracker:
    global _cost_tracker
    if _cost_tracker is None:
        _cost_tracker = CostTracker()
    return _cost_tracker
