"""Memoria de proyecto: GMINI.md / AGENTS.md / CLAUDE.md del repositorio en el que trabaja el agente."""

from __future__ import annotations

from backend.core import action_output
from backend.core.planner import Action, ActionPlanner
from backend.core.workspace_manager import WorkspaceManager


def _repo(tmp_path):
    repo = tmp_path / "mi-app"
    (repo / "src").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname = 'mi-app'\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("Usa pytest -q. Nada de print en src/.", encoding="utf-8")
    (repo / "CLAUDE.md").write_text("Commits en español.", encoding="utf-8")
    return repo


def test_snapshot_lists_and_reads_the_instructions(tmp_path):
    repo = _repo(tmp_path)
    manager = WorkspaceManager(root_dir=tmp_path)
    snapshot = manager.workspace_snapshot(str(repo / "src"))
    assert snapshot["project_instructions_files"] == ["AGENTS.md", "CLAUDE.md"]
    data = manager.project_instructions(str(repo / "src"))
    assert [f["file"] for f in data["files"]] == ["AGENTS.md", "CLAUDE.md"]
    assert "pytest -q" in data["files"][0]["text"]


async def test_action_result_goes_in_as_project_instructions(tmp_path):
    repo = _repo(tmp_path)
    planner = ActionPlanner.__new__(ActionPlanner)
    planner._workspace = WorkspaceManager(root_dir=tmp_path)
    result = await planner._execute_single(Action(type="project_instructions", params={"path": str(repo)}))
    assert result["success"] and "AGENTS.md" in result["message"]

    block = action_output.build_results_block([result, {"action": "file_read_text", "data": {"content": "x = 1"}}])
    project_part, data_part = block.split("Contenido devuelto por las herramientas")
    assert "<proyecto>" in project_part and "pytest -q" in project_part and "no anulan" in project_part
    assert "x = 1" in data_part


async def test_project_without_instructions(tmp_path):
    repo = tmp_path / "vacio"
    repo.mkdir()
    (repo / "package.json").write_text("{}", encoding="utf-8")
    planner = ActionPlanner.__new__(ActionPlanner)
    planner._workspace = WorkspaceManager(root_dir=tmp_path)
    result = await planner._execute_single(Action(type="project_instructions", params={"path": str(repo)}))
    assert not result["success"] and "no tiene" in result["message"]
