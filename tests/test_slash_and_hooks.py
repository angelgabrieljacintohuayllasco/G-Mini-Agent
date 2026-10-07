"""Comandos "/", modo plan, hooks de acciones y protección de los archivos propios de G-Mini."""

from __future__ import annotations

import sys

import pytest

from backend.core import action_hooks, slash_commands


class _Agent:
    def __init__(self):
        self.current_mode = "normal"
        self.new_sessions = 0
        self.applied = 0
        self.stopped = False

    async def new_session(self):
        self.new_sessions += 1

    def set_mode(self, key):
        self.current_mode = key
        return {"current_mode_name": key.capitalize()}

    def _apply_system_prompt(self):
        self.applied += 1

    async def stop(self):
        self.stopped = True


async def test_plain_text_and_paths_are_not_commands():
    agent = _Agent()
    assert await slash_commands.handle(agent, "hola") is None
    assert await slash_commands.handle(agent, "/home/user/archivo.txt") is None
    assert await slash_commands.handle(agent, "//comentario") is None


async def test_builtins_answer_without_the_model():
    agent = _Agent()
    help_text = (await slash_commands.handle(agent, "/ayuda")).reply
    assert "/plan <tarea>" in help_text and "/olvidar <id>" in help_text
    assert (await slash_commands.handle(agent, "/new")).reply and agent.new_sessions == 1
    assert "Programador" in (await slash_commands.handle(agent, "/modo programador")).reply
    assert agent.current_mode == "programador"
    assert "No existe" in (await slash_commands.handle(agent, "/modo inventado")).reply
    assert "No conozco" in (await slash_commands.handle(agent, "/xyz")).reply
    await slash_commands.handle(agent, "/detener")
    assert agent.stopped


async def test_plan_and_skill_build_prompts():
    agent = _Agent()
    plan = await slash_commands.handle(agent, "/plan ordenar mis descargas")
    assert plan.plan_mode and plan.prompt == "ordenar mis descargas" and plan.reply is None
    skill = await slash_commands.handle(agent, "/skill correo-profesional responder al cliente")
    assert "skill_read" in skill.prompt and "correo-profesional" in skill.prompt


async def test_memory_commands(monkeypatch):
    from backend.core.memory_ltm import get_ltm

    agent = _Agent()
    mid = get_ltm().store("El usuario prefiere respuestas breves", "preference")
    listed = (await slash_commands.handle(agent, "/recuerdos")).reply
    assert mid in listed and "breves" in listed
    assert "Olvidado" in (await slash_commands.handle(agent, f"/olvidar {mid}")).reply
    assert get_ltm().get(mid) is None and agent.applied == 1


async def test_custom_markdown_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(slash_commands, "COMMANDS_DIR", tmp_path)
    (tmp_path / "resumir.md").write_text("# Resume\nResume en 3 puntos: $ARGUMENTS", encoding="utf-8")
    (tmp_path / "saludo.md").write_text("Saluda con cariño", encoding="utf-8")
    result = await slash_commands.handle(_Agent(), "/resumir el informe de ventas")
    assert result.prompt.endswith("Resume en 3 puntos: el informe de ventas")
    assert (await slash_commands.handle(_Agent(), "/saludo a Ana")).prompt == "Saluda con cariño\n\na Ana"
    assert "/resumir: Resume" in (await slash_commands.handle(_Agent(), "/ayuda")).reply


def test_shipped_commands_exist():
    from backend.config import CODE_DIR

    shipped = {p.stem for p in (CODE_DIR / "data" / "commands").glob("*.md")}
    assert {"resumir", "traducir", "revisar-codigo"} <= shipped


# ── Hooks ──────────────────────────────────────────────────────────────

def _hook_script(tmp_path, body: str) -> list[str]:
    script = tmp_path / "hook.py"
    script.write_text(body, encoding="utf-8")
    return [sys.executable, str(script)]


async def test_pre_hook_blocks_with_exit_code_2(tmp_path, monkeypatch):
    command = _hook_script(tmp_path, (
        "import json, sys\n"
        "data = json.loads(sys.stdin.read())\n"
        "if 'rm' in data['params'].get('command', ''):\n"
        "    sys.stderr.write('comando destructivo'); sys.exit(2)\n"
    ))
    monkeypatch.setattr(action_hooks.config, "get", lambda *k, default=None: (
        [{"match": "terminal_*", "command": command}] if k == ("hooks", "pre_action") else default))
    assert await action_hooks.run_pre("terminal_run", {"command": "rm -rf x"}) == "comando destructivo"
    assert await action_hooks.run_pre("terminal_run", {"command": "dir"}) is None
    assert await action_hooks.run_pre("file_read_text", {"command": "rm"}) is None  # no coincide


async def test_post_hook_receives_the_result(tmp_path, monkeypatch):
    log = tmp_path / "log.json"
    command = _hook_script(tmp_path, f"import sys; open(r'{log}', 'w').write(sys.stdin.read())\n")
    monkeypatch.setattr(action_hooks.config, "get", lambda *k, default=None: (
        [{"match": "*", "command": command}] if k == ("hooks", "post_action") else default))
    await action_hooks.run_post("file_write_text", {"path": "a.txt"}, {"success": True, "message": "ok", "data": "x"})
    import json

    payload = json.loads(log.read_text())
    assert payload["event"] == "post_action" and payload["result"] == {"success": True, "message": "ok"}


async def test_planner_respects_hook_block(monkeypatch):
    from backend.core.planner import Action, ActionPlanner

    async def block(action_type, params):
        return "no permitido"

    monkeypatch.setattr(action_hooks, "run_pre", block)
    planner = ActionPlanner.__new__(ActionPlanner)
    ran = []

    async def fake_exec(action):
        ran.append(action)
        return {"action": action.type, "success": True, "message": ""}

    planner._execute_with_resilience = fake_exec
    results = await planner.execute_actions([Action(type="wait", params={"seconds": 1})])
    assert ran == [] and "Bloqueada por un hook: no permitido" in results[0]["message"]


# ── Protección de los propios archivos ─────────────────────────────────

@pytest.mark.parametrize("rel", ["config.user.yaml", "data/runtime/session_token", "backend/core/policy.py",
                                 "data/skills/web-search/tools/x.py", "data/agent_skills/agent/x/SKILL.md"])
def test_agent_cannot_write_its_own_config_code_or_skills(rel):
    from backend.config import CODE_DIR
    from backend.core.workspace_manager import PathAccessDeniedError, WorkspaceManager

    manager = WorkspaceManager.__new__(WorkspaceManager)
    manager._allowed_write_dirs = [CODE_DIR]
    with pytest.raises(PathAccessDeniedError):
        manager._check_write_access((CODE_DIR / rel).resolve())
