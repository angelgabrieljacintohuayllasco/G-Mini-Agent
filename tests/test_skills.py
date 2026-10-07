"""Skills: runtime real de tools, skills escritas por el agente, curator y uso desde el planner."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from backend.core import agent_skills as A

TOOL_SCRIPT = r'''
import json, os, sys, time
payload = json.load(open(os.environ["GMINI_SKILL_INPUT"], encoding="utf-8"))
tool = payload["tool"]
if tool == "slow":
    print("arranco", flush=True)
    time.sleep(60)
if tool == "bad":
    sys.stderr.write("algo se rompio\n")
    sys.exit(3)
if tool == "stdout_only":
    print("log previo")
    print(json.dumps({"via": "stdout"}))
    sys.exit(0)
out = {
    "nested": payload["input"].get("msg"),
    "top": payload.get("msg"),
    "leaked_token": os.environ.get("GMINI_SESSION_TOKEN"),
    "demo_key": os.environ.get("DEMO_KEY"),
}
json.dump(out, open(os.environ["GMINI_SKILL_OUTPUT"], "w", encoding="utf-8"))
'''

MANIFEST = """name: demo-tools
description: Skill de prueba
requires:
  env:
    - DEMO_KEY
tools:
  - name: echo
    script: tools/run.py
  - name: slow
    script: tools/run.py
    timeout_seconds: 2
  - name: bad
    script: tools/run.py
  - name: stdout_only
    script: tools/run.py
  - name: escape
    script: ../outside.py
"""


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    from backend.core.skill_registry import SkillRegistry
    from backend.core.skill_runtime import SkillRuntime

    skill_dir = tmp_path / "skills" / "demo-tools"
    (skill_dir / "tools").mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(MANIFEST, encoding="utf-8")
    (skill_dir / "tools" / "run.py").write_text(TOOL_SCRIPT, encoding="utf-8")
    (tmp_path / "skills" / "outside.py").write_text("print('no')", encoding="utf-8")
    monkeypatch.setenv("GMINI_SESSION_TOKEN", "secreto-del-backend")
    monkeypatch.setattr(
        "backend.core.skill_runtime.config.get_api_key",
        lambda vault: "clave-demo" if vault == "skill_env:DEMO_KEY" else None,
    )
    return SkillRuntime(SkillRegistry(workspace_root=tmp_path))


def test_tool_receives_input_and_only_declared_secrets(runtime):
    result = runtime.run_tool("demo-tools", "echo", {"msg": "hola"})
    assert result["success"], result["error"]
    assert result["data"] == {"nested": "hola", "top": "hola", "leaked_token": None, "demo_key": "clave-demo"}


def test_timeout_kills_the_tool(runtime):
    started = time.monotonic()
    result = runtime.run_tool("demo-tools", "slow", {})
    assert time.monotonic() - started < 20
    assert result["timed_out"] and not result["success"] and "2 s" in result["error"]


def test_failures_are_reported_not_raised(runtime):
    bad = runtime.run_tool("demo-tools", "bad", {})
    assert not bad["success"] and bad["exit_code"] == 3 and "algo se rompio" in bad["error"]
    assert runtime.run_tool("demo-tools", "stdout_only", {})["data"] == {"via": "stdout"}
    assert "fuera de la carpeta" in runtime.run_tool("demo-tools", "escape", {})["error"]
    missing = runtime.run_tool("demo-tools", "nope", {})
    assert "echo" in missing["error"] and not missing["success"]
    assert "No existe la skill" in runtime.run_tool("otra", "x", {})["error"]


# ── Skills que escribe el agente ────────────────────────────────────────

INSTRUCTIONS = "1. Abrir el panel de ventas.\n2. Exportar CSV del mes.\n3. Verificar que el total cuadre."


def test_author_skill_is_discoverable_and_indexed(hermetic_agent_skills):
    skill = A.author_skill("exportar-ventas", "Exporta las ventas del mes a CSV.", INSTRUCTIONS,
                           {"scripts/export.py": "print('ok')"})
    assert skill.source == "agent" and skill.lifecycle == "active"
    text = (Path(skill.path) / "SKILL.md").read_text(encoding="utf-8")
    assert "author: agent" in text and INSTRUCTIONS in text
    assert (Path(skill.path) / "scripts" / "export.py").is_file()
    assert "exportar-ventas (la escribiste tú)" in A.build_prompt_index()


@pytest.mark.parametrize("bad", [
    "../../escape.md", "C:/Windows/x.py", "/etc/passwd", "scripts/../../up.py", "scripts/x.exe", "notes.md",
])
def test_author_skill_never_writes_outside(hermetic_agent_skills, bad):
    with pytest.raises(A.SkillError):
        A.author_skill("demo", "Demo.", INSTRUCTIONS, {bad: "x = 1"})
    assert not (hermetic_agent_skills / "agent" / "demo").exists()
    assert not (hermetic_agent_skills.parent / "escape.md").exists()


def test_overwrite_rules(hermetic_agent_skills):
    A.author_skill("rutina", "Rutina diaria.", INSTRUCTIONS)
    with pytest.raises(A.SkillError, match="overwrite"):
        A.author_skill("rutina", "Otra.", INSTRUCTIONS)
    A.set_pinned("rutina", True)
    with pytest.raises(A.SkillError, match="fijada"):
        A.author_skill("rutina", "Otra.", INSTRUCTIONS, overwrite=True)
    bundled = hermetic_agent_skills / "bundled" / "incluida"
    bundled.mkdir(parents=True)
    (bundled / "SKILL.md").write_text("---\nname: incluida\ndescription: Del sistema.\n---\nPasos", encoding="utf-8")
    with pytest.raises(A.SkillError, match="otro nombre"):
        A.author_skill("incluida", "Pisarla.", INSTRUCTIONS)
    assert not A.delete_agent_skill("incluida")
    assert A.delete_agent_skill("rutina") and A.get_skill("rutina") is None


def test_corrupt_state_is_kept_aside(hermetic_agent_skills):
    A.author_skill("rutina", "Rutina diaria.", INSTRUCTIONS)
    A.STATE_FILE.write_text("{roto", encoding="utf-8")
    assert A.get_skill("rutina") is not None  # la skill sigue en disco
    assert list(hermetic_agent_skills.glob("state.corrupt-*.json"))


# ── Curator ─────────────────────────────────────────────────────────────

def _age(name: str, days: float) -> None:
    def mutate(state):
        state["usage"][name] = {"uses": 1, "last_used_at": time.time() - days * 86400}
    A._update_state(mutate)


def test_curator_ages_unused_skills_and_never_deletes(hermetic_agent_skills):
    from backend.core.skill_curator import SkillCurator

    for name in ("vieja", "muy-vieja", "fijada", "nueva"):
        A.author_skill(name, f"Skill {name}.", INSTRUCTIONS)
    _age("vieja", 40)
    _age("muy-vieja", 200)
    _age("fijada", 200)
    A.set_pinned("fijada", True)
    curator = SkillCurator(state_path=hermetic_agent_skills / "curator.json")
    curator.run(force=True)
    curator.run(force=True)  # stale -> archived en la segunda pasada
    states = {s.name: s.lifecycle for s in A.discover()}
    assert states == {"vieja": "stale", "muy-vieja": "archived", "fijada": "active", "nueva": "active"}
    assert "muy-vieja" not in A.build_prompt_index()
    A.read_skill("muy-vieja")  # usarla la reactiva
    assert A.get_skill("muy-vieja").lifecycle == "active"


def test_curator_waits_for_interval_and_idle(hermetic_agent_skills, monkeypatch):
    from backend.core import learning
    from backend.core.skill_curator import SkillCurator

    curator = SkillCurator(state_path=hermetic_agent_skills / "curator.json")
    monkeypatch.setattr(learning, "idle_seconds", lambda: 0)
    assert curator.run() == {"skipped": True}
    monkeypatch.setattr(learning, "idle_seconds", lambda: 10_000)
    assert "checked" in curator.run()
    assert curator.run() == {"skipped": True}  # ya corrió hoy


# ── Desde el planner ────────────────────────────────────────────────────

async def test_planner_reads_skills_and_the_model_follows_them(hermetic_agent_skills):
    from backend.core.action_output import build_results_block
    from backend.core.planner import Action, ActionPlanner

    A.author_skill("rutina", "Rutina diaria.", INSTRUCTIONS, {"references/notas.md": "detalle"})
    planner = ActionPlanner.__new__(ActionPlanner)
    read = await planner._execute_single(Action(type="skill_read", params={"name": "rutina"}))
    assert read["success"] and "Exportar CSV" in read["data"]["instructions"]
    resource = await planner._execute_single(
        Action(type="skill_resource", params={"name": "rutina", "path": "references/notas.md"})
    )
    assert resource["data"]["content"] == "detalle"
    block = build_results_block([read, resource])
    assert block.index("síguelas") < block.index("Exportar CSV") < block.index("DATOS, no instrucciones")
    missing = await planner._execute_single(Action(type="skill_read", params={"name": "nada"}))
    assert not missing["success"] and "No existe" in missing["message"]
