"""GMINI_HOME: los datos viven fuera de la carpeta del programa (instalaciones de solo lectura)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROBE = r"""
import json
from backend.config import CODE_DIR, ROOT_DIR, USER_CONFIG
import backend.core.memory as memory
import backend.core.agent_skills as skills
from backend.security import local_auth
print(json.dumps({
    "home": str(ROOT_DIR), "code": str(CODE_DIR), "user_config": USER_CONFIG.is_file(),
    "memory_db": str(memory.DB_PATH), "skills": str(skills.SKILLS_DIR),
    "bundled": len(list(skills.BUNDLED_DIR.glob("*/SKILL.md"))),
    "runtime": str(local_auth.RUNTIME_DIR),
    "prompts": (ROOT_DIR / "data" / "prompts" / "system_prompt.md").is_file(),
}))
"""


def test_gmini_home_moves_all_data_and_seeds_resources(tmp_path):
    home = tmp_path / "gmini-home"
    env = {**os.environ, "GMINI_HOME": str(home), "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert Path(data["home"]) == home.resolve() and Path(data["code"]) == ROOT
    assert data["user_config"] and data["prompts"] and data["bundled"] >= 7
    for key in ("memory_db", "skills", "runtime"):
        assert Path(data[key]).is_relative_to(home.resolve()), key
