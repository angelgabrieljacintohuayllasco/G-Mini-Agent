"""El cron se interpreta en la zona del usuario: "0 7 * * *" son las 7 de Lima, no de UTC."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core import scheduler as scheduler_module
from backend.core.scheduler import SchedulerService

FROM = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)  # 05:00 en Lima


def _next(service, tz_name=None):
    return service._compute_next_run(trigger_type="cron", interval_seconds=None, cron_expression="0 7 * * *",
                                     heartbeat_interval_seconds=None, from_dt=FROM, tz_name=tz_name)


def test_cron_uses_the_job_timezone(tmp_path):
    service = SchedulerService(db_path=tmp_path / "scheduler.db")
    assert _next(service, "America/Lima") == datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    assert _next(service, "Europe/Madrid") == datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc)  # CEST


def test_without_job_timezone_uses_app_timezone(tmp_path, monkeypatch):
    real_get = scheduler_module.config.get
    monkeypatch.setattr(scheduler_module.config, "get", lambda *k, default=None: (
        "America/Lima" if k == ("app", "timezone") else real_get(*k, default=default)))
    service = SchedulerService(db_path=tmp_path / "scheduler.db")
    assert _next(service) == datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    assert _next(service, "Zona/Inventada") == datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


async def test_jobs_store_their_timezone(tmp_path):
    service = SchedulerService(db_path=tmp_path / "scheduler.db")
    await service.initialize()
    job = await service.create_job(name="Resumen", task_type="agent_prompt", payload={"task_id": "x"},
                                   trigger_type="cron", cron_expression="0 7 * * *", timezone_name="America/Lima")
    assert job["timezone"] == "America/Lima"
    next_run = datetime.fromisoformat(job["next_run_at"]).astimezone(timezone.utc)
    assert (next_run.hour, next_run.minute) == (12, 0)
    updated = await service.update_job(job["job_id"], timezone_name="Asia/Tokyo")
    next_run = datetime.fromisoformat(updated["next_run_at"]).astimezone(timezone.utc)
    assert updated["timezone"] == "Asia/Tokyo" and (next_run.hour, next_run.minute) == (22, 0)
