"""
G-Mini Agent - Skill registry.
Descubre y valida skills locales/configuradas sin acoplar el core a un marketplace.
"""

from __future__ import annotations

import json
import os
import stat
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from backend.config import ROOT_DIR, config

SKILL_MANIFEST_NAMES = ("skill.yaml", "skill.yml", "skill.json")
README_CANDIDATES = ("README.md", "README.MD", "readme.md")
README_EXCERPT_LIMIT = 4000
LOCAL_SKILLS_ROOT = Path.home() / ".gmini" / "skills"


def _expand_path(raw_path: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(str(raw_path or "").strip()))
    return Path(expanded)


def _normalize_skill_id(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "-" for char in str(value or "").strip()).strip("-")


def _coerce_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


@dataclass(slots=True)
class SkillRoot:
    source: str
    path: Path
    priority: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "path": str(self.path),
            "priority": self.priority,
            "exists": self.path.exists(),
        }


@dataclass(slots=True)
class SkillDescriptor:
    id: str
    name: str
    version: str
    description: str
    category: str
    author: str
    source: str
    priority: int
    enabled: bool
    root_path: Path
    manifest_path: Path
    readme_path: Path | None = None
    readme_excerpt: str | None = None
    requires_api_keys: list[str] = field(default_factory=list)
    requires_env: list[str] = field(default_factory=list)
    requires_permissions: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "category": self.category,
            "author": self.author,
            "source": self.source,
            "priority": self.priority,
            "enabled": self.enabled,
            "root_path": str(self.root_path),
            "manifest_path": str(self.manifest_path),
            "readme_path": str(self.readme_path) if self.readme_path else None,
            "readme_excerpt": self.readme_excerpt,
            "requires_api_keys": list(self.requires_api_keys),
            "requires_env": list(self.requires_env),
            "requires_permissions": list(self.requires_permissions),
            "tools": list(self.tools),
            "errors": list(self.errors),
        }


class SkillRegistry:
    """Descubre skills desde roots conocidos y rutas configuradas por el usuario."""

    def __init__(self, workspace_root: Path | None = None):
        self._workspace_root = Path(workspace_root or ROOT_DIR)

    def list_catalog(self) -> dict[str, Any]:
        roots = self._discover_roots()
        selected: dict[str, SkillDescriptor] = {}
        duplicates: dict[str, list[str]] = {}
        disabled_ids = {
            _normalize_skill_id(item)
            for item in (config.get("skills", "disabled_ids", default=[]) or [])
            if str(item).strip()
        }

        for root in roots:
            if not root.path.exists():
                continue

            for skill_path in self._iter_skill_paths(root.path):
                descriptor = self._load_skill(skill_path, root, disabled_ids=disabled_ids)
                if descriptor is None:
                    continue

                current = selected.get(descriptor.id)
                if current is None:
                    selected[descriptor.id] = descriptor
                    continue

                if descriptor.priority > current.priority:
                    duplicates.setdefault(descriptor.id, []).append(str(current.root_path))
                    selected[descriptor.id] = descriptor
                else:
                    duplicates.setdefault(descriptor.id, []).append(str(descriptor.root_path))

        skills = sorted(
            (item.as_dict() for item in selected.values()),
            key=lambda item: (-int(item["priority"]), item["name"].lower(), item["id"]),
        )
        return {
            "enabled": bool(config.get("skills", "enabled", default=True)),
            "roots": [root.as_dict() for root in roots],
            "skills": skills,
            "duplicates": duplicates,
        }

    def get_skill(self, skill_id: str) -> dict[str, Any]:
        normalized_id = _normalize_skill_id(skill_id)
        catalog = self.list_catalog()
        for skill in catalog["skills"]:
            if skill["id"] == normalized_id:
                return skill
        raise KeyError(skill_id)

    def install_from_path(self, source_path: str, overwrite: bool = False) -> dict[str, Any]:
        skill_source = self._resolve_install_source(_expand_path(source_path))
        descriptor = self._load_skill(skill_source, SkillRoot("install", skill_source, 999))
        if descriptor is None or descriptor.errors:
            details = "; ".join(descriptor.errors) if descriptor else "Skill invalida o no encontrada."
            raise ValueError(details)

        target_root = self._ensure_local_root()
        target_path = target_root / descriptor.id
        if target_path.exists():
            if not overwrite:
                raise FileExistsError(f"La skill '{descriptor.id}' ya existe en {target_path}")
            shutil.rmtree(target_path)

        shutil.copytree(
            skill_source,
            target_path,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
        return self.get_skill(descriptor.id)

    def install_from_git(
        self,
        repo_url: str,
        ref: str | None = None,
        subdir: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        git_bin = shutil.which("git")
        if not git_bin:
            raise RuntimeError("Git no esta disponible para instalar la skill desde repo.")

        with tempfile.TemporaryDirectory(prefix="gmini-skill-") as tmp_dir:
            repo_path = Path(tmp_dir) / "repo"
            command = [git_bin, "clone", "--depth", "1"]
            if ref:
                if ref.startswith("-"):
                    raise ValueError(f"Git ref inválido: {ref}")
                command.extend(["--branch", ref])
            command.extend(["--", repo_url, str(repo_path)])
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip() or "git clone fallo"
                raise RuntimeError(detail)

            install_path = repo_path / str(subdir or "").strip() if subdir else repo_path
            return self.install_from_path(str(install_path), overwrite=overwrite)

    def set_enabled(self, skill_id: str, enabled: bool) -> dict[str, Any]:
        normalized_id = _normalize_skill_id(skill_id)
        _ = self.get_skill(normalized_id)
        disabled_ids = [
            _normalize_skill_id(item)
            for item in (config.get("skills", "disabled_ids", default=[]) or [])
            if str(item).strip()
        ]

        if enabled:
            disabled_ids = [item for item in disabled_ids if item != normalized_id]
        elif normalized_id not in disabled_ids:
            disabled_ids.append(normalized_id)

        config.set("skills", "disabled_ids", value=disabled_ids)
        return self.get_skill(normalized_id)

    def uninstall(self, skill_id: str) -> dict[str, Any]:
        skill = self.get_skill(skill_id)
        skill_path = Path(skill["root_path"])
        local_root = self._ensure_local_root()

        try:
            skill_path.relative_to(local_root)
        except ValueError as exc:
            raise ValueError(
                "Solo se pueden desinstalar skills instaladas en el root local de G-Mini. "
                "Las skills custom externas se gestionan quitando su ruta de configuracion."
            ) from exc

        if skill_path.exists():
            self._remove_tree(skill_path)

        disabled_ids = [
            _normalize_skill_id(item)
            for item in (config.get("skills", "disabled_ids", default=[]) or [])
            if _normalize_skill_id(item) != skill["id"]
        ]
        config.set("skills", "disabled_ids", value=disabled_ids)
        return {
            "id": skill["id"],
            "name": skill["name"],
            "root_path": str(skill_path),
            "deleted": True,
        }

    def _discover_roots(self) -> list[SkillRoot]:
        custom_paths = config.get("skills", "custom_paths", default=[]) or []
        roots: list[SkillRoot] = [
            SkillRoot("workspace", self._workspace_root / "skills", 400),
        ]

        custom_priority = 320
        for raw_path in custom_paths:
            roots.append(SkillRoot("custom", _expand_path(str(raw_path)), custom_priority))
            custom_priority -= 5

        roots.append(SkillRoot("local", LOCAL_SKILLS_ROOT, 250))
        roots.append(SkillRoot("bundled", self._workspace_root / "data" / "skills", 100))

        deduped: list[SkillRoot] = []
        seen: set[str] = set()
        for root in roots:
            key = str(root.path.resolve(strict=False)).lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(root)
        return deduped

    def _iter_skill_paths(self, root_path: Path) -> list[Path]:
        if self._find_manifest_path(root_path):
            return [root_path]

        candidates: list[Path] = []
        try:
            for child in sorted(root_path.iterdir(), key=lambda item: item.name.lower()):
                if child.is_dir() and self._find_manifest_path(child):
                    candidates.append(child)
        except OSError:
            return []
        return candidates

    def _find_manifest_path(self, skill_path: Path) -> Path | None:
        for manifest_name in SKILL_MANIFEST_NAMES:
            manifest_path = skill_path / manifest_name
            if manifest_path.exists() and manifest_path.is_file():
                return manifest_path
        return None

    def _find_readme_path(self, skill_path: Path) -> Path | None:
        for candidate in README_CANDIDATES:
            readme_path = skill_path / candidate
            if readme_path.exists() and readme_path.is_file():
                return readme_path
        return None

    def _load_skill(
        self,
        skill_path: Path,
        root: SkillRoot,
        disabled_ids: set[str] | None = None,
    ) -> SkillDescriptor | None:
        manifest_path = self._find_manifest_path(skill_path)
        if manifest_path is None:
            return None

        errors: list[str] = []
        try:
            if manifest_path.suffix.lower() == ".json":
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            else:
                manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            errors.append(f"Manifest invalido: {exc}")
            manifest_data = {}

        raw_name = str(manifest_data.get("name") or skill_path.name).strip() or skill_path.name
        skill_id = _normalize_skill_id(str(manifest_data.get("id") or raw_name or skill_path.name))
        if not skill_id:
            skill_id = _normalize_skill_id(skill_path.name) or "skill"
            errors.append("No se pudo inferir un id valido; se uso el nombre de carpeta.")

        requires = manifest_data.get("requires") if isinstance(manifest_data.get("requires"), dict) else {}
        tools = self._normalize_tools(manifest_data.get("tools"))
        readme_path = self._find_readme_path(skill_path)
        readme_excerpt = None
        if readme_path is not None:
            try:
                readme_excerpt = readme_path.read_text(encoding="utf-8")[:README_EXCERPT_LIMIT]
            except Exception as exc:
                errors.append(f"No se pudo leer README: {exc}")

        return SkillDescriptor(
            id=skill_id,
            name=raw_name,
            version=str(manifest_data.get("version") or ""),
            description=str(manifest_data.get("description") or ""),
            category=str(manifest_data.get("category") or ""),
            author=str(manifest_data.get("author") or ""),
            source=root.source,
            priority=root.priority,
            enabled=bool(manifest_data.get("enabled", True)) and skill_id not in (disabled_ids or set()),
            root_path=skill_path,
            manifest_path=manifest_path,
            readme_path=readme_path,
            readme_excerpt=readme_excerpt,
            requires_api_keys=_coerce_list(requires.get("api_keys")),
            requires_env=_coerce_list(requires.get("env")),
            requires_permissions=_coerce_list(requires.get("permissions")),
            tools=tools,
            errors=errors,
        )

    def _normalize_tools(self, raw_tools: Any) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        if not isinstance(raw_tools, list):
            return tools

        for item in raw_tools:
            if isinstance(item, str):
                name = item.strip()
                if name:
                    tools.append({"name": name, "description": ""})
                continue
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("id") or "").strip()
                if not name:
                    continue
                timeout_seconds = item.get("timeout_seconds")
                try:
                    normalized_timeout = int(timeout_seconds) if timeout_seconds is not None else None
                except (TypeError, ValueError):
                    normalized_timeout = None
                tools.append(
                    {
                        "name": name,
                        "description": str(item.get("description") or "").strip(),
                        "script": str(item.get("script") or "").strip() or None,
                        "entrypoint": str(item.get("entrypoint") or "").strip() or None,
                        "command": item.get("command") if isinstance(item.get("command"), list) else str(item.get("command") or "").strip() or None,
                        "timeout_seconds": normalized_timeout,
                    }
                )
        return tools

    def _ensure_local_root(self) -> Path:
        LOCAL_SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
        return LOCAL_SKILLS_ROOT

    def _remove_tree(self, target_path: Path) -> None:
        def _onerror(func, path, exc_info):
            os.chmod(path, stat.S_IWRITE)
            func(path)

        shutil.rmtree(target_path, onerror=_onerror)

    def _resolve_install_source(self, source_path: Path) -> Path:
        if not source_path.exists():
            raise FileNotFoundError(f"No existe la ruta de skill: {source_path}")
        if not source_path.is_dir():
            raise ValueError(f"La ruta de skill debe ser una carpeta: {source_path}")
        if self._find_manifest_path(source_path):
            return source_path

        candidates = self._iter_skill_paths(source_path)
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise ValueError("No se encontro un manifest de skill en la ruta indicada.")
        raise ValueError(
            "La ruta contiene varias skills. Indica una carpeta concreta o usa subdir para seleccionar una."
        )
