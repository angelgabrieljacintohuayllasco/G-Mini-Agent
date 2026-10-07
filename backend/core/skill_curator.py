"""
G-Mini Agent — Curator de las skills que escribe el agente.

Las skills del modo aprendiz envejecen según el uso:
  active   -> stale     tras curator.stale_after_days sin usarse (30)
  stale    -> archived  tras curator.archive_after_days (90): sale del índice del prompt
Nunca borra nada, no toca skills fijadas ni las que instaló el usuario, y una
skill archivada vuelve a active apenas se usa (agent_skills.mark_used).

Corre como job del scheduler cada hora; solo actúa una vez por
curator.interval_hours y con el agente inactivo.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

from backend.config import config
from backend.core import agent_skills

DAY = 86400


class SkillCurator:
    def __init__(self, store=agent_skills, state_path: Path | None = None) -> None:
        self._store = store
        self._state_path = state_path or (agent_skills.SKILLS_DIR / "curator.json")

    def _cfg(self, key: str, default: float) -> float:
        try:
            return float(config.get("curator", key, default=default))
        except (TypeError, ValueError):
            return default

    def apply_automatic_transitions(self, now: float | None = None) -> dict[str, int]:
        now = now or time.time()
        stale_after = self._cfg("stale_after_days", 30) * DAY
        archive_after = self._cfg("archive_after_days", 90) * DAY
        counts = {"checked": 0, "stale": 0, "archived": 0}
        for skill in self._store.discover():
            if skill.source != "agent" or skill.pinned:
                continue
            counts["checked"] += 1
            idle = now - (skill.last_used_at or skill.created_at or now)
            if skill.lifecycle == "active" and idle >= stale_after:
                self._store.set_lifecycle(skill.name, "stale")
                counts["stale"] += 1
            elif skill.lifecycle == "stale" and idle >= archive_after:
                self._store.set_lifecycle(skill.name, "archived")
                counts["archived"] += 1
        return counts

    def _last_run(self) -> float:
        try:
            return float(json.loads(self._state_path.read_text(encoding="utf-8")).get("last_run", 0))
        except (OSError, ValueError, TypeError):
            return 0.0

    def should_run_now(self, now: float | None = None) -> bool:
        now = now or time.time()
        if not config.get("curator", "enabled", default=True):
            return False
        if now - self._last_run() < self._cfg("interval_hours", 24) * 3600:
            return False
        from backend.core.learning import idle_seconds

        return idle_seconds() >= self._cfg("min_idle_minutes", 30) * 60

    def run(self, *, force: bool = False, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        if not force and not self.should_run_now(now):
            return {"skipped": True}
        result = self.apply_automatic_transitions(now)
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps({"last_run": now, "last_result": result}, ensure_ascii=False), encoding="utf-8"
        )
        if result["stale"] or result["archived"]:
            logger.info(f"Curator de skills: {result}")
        return result


_curator: SkillCurator | None = None


def get_curator() -> SkillCurator:
    global _curator
    if _curator is None:
        _curator = SkillCurator()
    return _curator
