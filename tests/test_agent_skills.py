"""Skills en formato SKILL.md: parsing, descubrimiento, instalación y seguridad."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from backend.core import agent_skills as A


@pytest.fixture()
def skills_home(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(A, "BUNDLED_DIR", tmp_path / "bundled")
    monkeypatch.setattr(A, "INSTALLED_DIR", tmp_path / "installed")
    monkeypatch.setattr(A, "AGENT_DIR", tmp_path / "agent")
    monkeypatch.setattr(A, "STATE_FILE", tmp_path / "state.json")
    return tmp_path


def _write_skill(base: Path, folder: str, name: str, description: str, body: str = "# Pasos\n1. Hacer") -> Path:
    path = base / folder
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n", encoding="utf-8"
    )
    return path


def test_parse_valid_frontmatter():
    meta, body = A.parse_skill_md("---\nname: demo\ndescription: Hace algo útil\n---\n# Cuerpo\n")
    assert A.validate_meta(meta) == ("demo", "Hace algo útil")
    assert body == "# Cuerpo"


def test_lenient_parser_accepts_unquoted_colons():
    text = "---\nname: extremo\ndescription: Modo extremo — prohíbe esto (nota: cada falla trae salida)\n---\nCuerpo"
    meta, _ = A.parse_skill_md(text)
    name, desc = A.validate_meta(meta)
    assert name == "extremo"
    assert "nota: cada falla" in desc


@pytest.mark.parametrize("bad", ["---\ndescription: sin nombre\n---\nx", "sin frontmatter", "---\nname: Mal Nombre\ndescription: x\n---\n"])
def test_invalid_skills_rejected(bad):
    with pytest.raises(A.SkillError):
        meta, _ = A.parse_skill_md(bad)
        A.validate_meta(meta)


def test_discover_and_installed_overrides_bundled(skills_home):
    _write_skill(skills_home / "bundled", "resumen", "resumen", "Versión incluida")
    _write_skill(skills_home / "installed" / "pack", "resumen", "resumen", "Versión instalada")
    _write_skill(skills_home / "bundled", "otra", "otra", "Otra skill")
    skills = {s.name: s for s in A.discover()}
    assert set(skills) == {"resumen", "otra"}
    assert skills["resumen"].description == "Versión instalada"
    assert skills["resumen"].source == "installed"


def test_disable_removes_from_prompt_index(skills_home):
    _write_skill(skills_home / "bundled", "alfa", "alfa", "Primera")
    _write_skill(skills_home / "bundled", "beta", "beta", "Segunda")
    assert "- alfa: Primera" in A.build_prompt_index()
    A.set_enabled("alfa", False)
    index = A.build_prompt_index()
    assert "- alfa:" not in index
    assert "- beta: Segunda" in index
    with pytest.raises(A.SkillError):
        A.read_skill("alfa")


def test_read_skill_and_resource_with_traversal_guard(skills_home):
    folder = _write_skill(skills_home / "bundled", "docs", "docs", "Con referencias")
    (folder / "references").mkdir()
    (folder / "references" / "guia.md").write_text("contenido de apoyo", encoding="utf-8")
    (skills_home / "secreto.txt").write_text("no", encoding="utf-8")
    data = A.read_skill("docs")
    assert data["instructions"].startswith("# Pasos")
    assert "references/guia.md" in data["resources"]
    assert A.read_resource("docs", "references/guia.md") == "contenido de apoyo"
    with pytest.raises(A.SkillError):
        A.read_resource("docs", "../../secreto.txt")


def test_install_from_folder_skips_invalid_and_records_pack(skills_home, tmp_path_factory):
    src = tmp_path_factory.mktemp("repo")
    _write_skill(src / "skills", "uno", "uno", "Skill uno")
    _write_skill(src / "skills", "dos", "dos", "Skill dos")
    bad = src / "skills" / "rota"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("sin frontmatter", encoding="utf-8")
    _write_skill(src / ".git" / "hooks", "oculta", "oculta", "No debe instalarse")

    result = A.install_from_folder(src, pack="Mi Pack!")
    assert result["pack"] == "mi-pack"
    assert sorted(result["installed"]) == ["dos", "uno"]
    assert len(result["skipped"]) == 1
    assert {p["pack"] for p in A.list_packs()} == {"mi-pack"}
    assert A.remove_pack("mi-pack") is True
    assert A.discover() == []


@pytest.mark.parametrize(
    "source,expected",
    [
        ("owner/repo", ("owner", "repo", None, "")),
        ("https://github.com/owner/repo.git", ("owner", "repo", None, "")),
        ("https://github.com/anthropics/skills/tree/main/skills/pdf", ("anthropics", "skills", "main", "skills/pdf")),
        ("https://github.com/o/r/blob/dev/a/b/SKILL.md", ("o", "r", "dev", "a/b")),
    ],
)
def test_parse_github_source(source, expected):
    assert A.parse_github_source(source) == expected


@pytest.mark.parametrize("source", ["https://gitlab.com/o/r", "file:///C:/x", "", "https://github.com/solo"])
def test_parse_github_source_rejects(source):
    with pytest.raises(A.SkillError):
        A.parse_github_source(source)


def test_zip_slip_is_blocked(skills_home):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("repo-main/../../escape.txt", "x")
    with pytest.raises(A.SkillError):
        A._extract_zip_safely(buf.getvalue(), A._tmp_root())
