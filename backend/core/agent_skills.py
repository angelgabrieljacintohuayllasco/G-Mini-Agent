"""
G-Mini Agent — Skills en formato SKILL.md (estándar Agent Skills).

Una skill es una carpeta con un SKILL.md:

    ---
    name: nombre-en-kebab
    description: Cuándo usarla y qué hace (una o dos frases).
    ---
    # Instrucciones en Markdown...

y opcionalmente references/, scripts/ o assets/. El agente solo ve el índice
(nombre + descripción) en su prompt; cuando una tarea calza, carga el cuerpo
con la acción `skill_read` (divulgación progresiva, ahorra tokens).

Ubicaciones:
  data/agent_skills/bundled/<skill>/            skills incluidas con la app
  data/agent_skills/installed/<pack>/<skill>/   skills importadas por el usuario
  data/agent_skills/agent/<skill>/              skills que escribió el propio agente

Las skills del agente (modo aprendiz) tienen ciclo de vida: active -> stale ->
archived según el uso (lo maneja skill_curator). Nunca se borran solas y una
skill fijada (pinned) no cambia de estado.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import tempfile
import threading
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from loguru import logger

from backend.config import ROOT_DIR  # noqa: E402 - GMINI_HOME si está definido

SKILLS_DIR = ROOT_DIR / "data" / "agent_skills"
BUNDLED_DIR = SKILLS_DIR / "bundled"
INSTALLED_DIR = SKILLS_DIR / "installed"
AGENT_DIR = SKILLS_DIR / "agent"
STATE_FILE = SKILLS_DIR / "state.json"
LIFECYCLES = ("active", "stale", "archived")
AGENT_FILE_SUFFIXES = {".md", ".txt", ".py", ".ps1", ".sh", ".js", ".json", ".yaml", ".yml", ".csv", ".toml"}
MAX_AGENT_FILES = 20
_state_lock = threading.RLock()

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_DESCRIPTION = 1024
MAX_SKILL_BYTES = 256 * 1024
MAX_RESOURCE_BYTES = 512 * 1024
MAX_ARCHIVE_BYTES = 40 * 1024 * 1024
MAX_SKILLS_PER_IMPORT = 200
FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", re.S)
RESOURCE_DIRS = ("references", "reference", "scripts", "assets", "templates", "examples")

SUGGESTED_SOURCES: list[dict[str, str]] = [
    {
        "label": "Dev skills (ingeniería de software)",
        "url": "https://github.com/angelgabrieljacintohuayllasco/clipboard-skills",
        "description": "37 skills: intake, feature, fixer, quality-gate, ui-design, deploy, seguridad y más.",
    },
    {
        "label": "Agent modes (trabajo largo e investigación)",
        "url": "https://github.com/angelgabrieljacintohuayllasco/agent-modes",
        "description": "maraton, extremly, investigacion, ver-video, mcp-master, obsidian-memory.",
    },
    {
        "label": "Marketing skills (Meta Ads y TikTok Ads)",
        "url": "https://github.com/angelgabrieljacintohuayllasco/marketing-skills",
        "description": "fb-ads y tiktok-ads: estrategia, configuración y diagnóstico de campañas.",
    },
    {
        "label": "Anthropic skills (documentos y diseño)",
        "url": "https://github.com/anthropics/skills",
        "description": "Skills de referencia del estándar: docx, pdf, pptx, xlsx, diseño y más.",
    },
]


class SkillError(ValueError):
    """Error de validación o instalación de una skill."""


@dataclass
class AgentSkill:
    name: str
    description: str
    path: str
    source: str                     # bundled | installed | agent
    pack: str = ""
    enabled: bool = True
    license: str = ""
    resources: list[str] = field(default_factory=list)
    size_bytes: int = 0
    lifecycle: str = "active"       # solo cambia en skills del agente
    pinned: bool = False
    uses: int = 0
    last_used_at: float = 0.0
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Parsing ──────────────────────────────────────────────────────────────

def parse_skill_md(text: str) -> tuple[dict[str, Any], str]:
    """Separa frontmatter YAML y cuerpo. Lanza SkillError si no es válido."""
    match = FRONTMATTER_RE.match(text.lstrip("﻿"))
    if not match:
        raise SkillError("SKILL.md sin frontmatter YAML (--- name/description ---).")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        # Muchas skills publicadas tienen descripciones sin comillas con ": " adentro.
        meta = _lenient_frontmatter(match.group(1))
        if not meta.get("name"):
            raise SkillError(f"Frontmatter YAML inválido: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillError("El frontmatter debe ser un mapa YAML.")
    body = text[match.end():].strip()
    return meta, body


_TOP_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):(?:\s+(.*)|\s*)$")


def _lenient_frontmatter(block: str) -> dict[str, Any]:
    """Lee `clave: valor` de primer nivel tomando todo lo que sigue al primer ':'."""
    meta: dict[str, Any] = {}
    current: str | None = None
    for line in block.splitlines():
        match = _TOP_KEY_RE.match(line)  # la clave empieza en la columna 0
        if match:
            current = match.group(1)
            meta[current] = (match.group(2) or "").strip()
        elif current and line.strip():
            meta[current] = f"{meta[current]} {line.strip()}".strip()
    for key, value in list(meta.items()):
        if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            meta[key] = value[1:-1]
    return meta


def validate_meta(meta: dict[str, Any], folder_name: str | None = None) -> tuple[str, str]:
    name = str(meta.get("name") or "").strip()
    description = " ".join(str(meta.get("description") or "").split())
    if not NAME_RE.match(name):
        raise SkillError(
            f"Nombre de skill inválido '{name}': usa minúsculas, números y guiones (máx. 64)."
        )
    if not description:
        raise SkillError(f"La skill '{name}' no tiene description.")
    if len(description) > MAX_DESCRIPTION:
        description = description[: MAX_DESCRIPTION - 3].rstrip() + "..."
    if folder_name and folder_name != name:
        logger.debug(f"agent_skills: carpeta '{folder_name}' != name '{name}' (se usa name)")
    return name, description


# ── Estado (habilitadas / packs instalados) ──────────────────────────────

def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key, default in (("disabled", []), ("packs", {}), ("agent", {}), ("usage", {})):
                data.setdefault(key, default)
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        # No se pisa un estado corrupto: se guarda aparte para no perder packs ni skills.
        backup = STATE_FILE.with_suffix(f".corrupt-{int(time.time())}.json")
        try:
            STATE_FILE.replace(backup)
        except OSError:
            pass
        logger.warning(f"agent_skills: state.json ilegible ({exc}); copia en {backup.name}")
    return {"disabled": [], "packs": {}, "agent": {}, "usage": {}}


def _save_state(state: dict[str, Any]) -> None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _update_state(mutate) -> dict[str, Any]:
    """Lee, modifica y guarda el estado bajo un lock (varios hilos lo tocan)."""
    with _state_lock:
        state = _load_state()
        mutate(state)
        _save_state(state)
        return state


# ── Descubrimiento ───────────────────────────────────────────────────────

def _list_resources(folder: Path) -> list[str]:
    out: list[str] = []
    for sub in RESOURCE_DIRS:
        base = folder / sub
        if not base.is_dir():
            continue
        for item in sorted(base.rglob("*")):
            if item.is_file() and "__pycache__" not in item.parts:
                out.append(item.relative_to(folder).as_posix())
    for item in sorted(folder.glob("*.md")):
        if item.name != "SKILL.md":
            out.append(item.name)
    return out[:200]


def _load_skill(
    folder: Path, source: str, pack: str, disabled: set[str], state: dict[str, Any] | None = None
) -> AgentSkill | None:
    skill_file = folder / "SKILL.md"
    try:
        raw = skill_file.read_bytes()
        if len(raw) > MAX_SKILL_BYTES:
            raise SkillError(f"SKILL.md demasiado grande ({len(raw)} bytes).")
        meta, _body = parse_skill_md(raw.decode("utf-8", errors="replace"))
        name, description = validate_meta(meta, folder.name)
    except (OSError, SkillError) as exc:
        logger.warning(f"agent_skills: se ignora {skill_file}: {exc}")
        return None
    state = state or {}
    usage = (state.get("usage") or {}).get(name, {})
    agent_meta = (state.get("agent") or {}).get(name, {}) if source == "agent" else {}
    return AgentSkill(
        name=name,
        description=description,
        path=str(folder),
        source=source,
        pack=pack,
        enabled=name not in disabled,
        license=str(meta.get("license") or ""),
        resources=_list_resources(folder),
        size_bytes=len(raw),
        lifecycle=str(agent_meta.get("lifecycle") or "active"),
        pinned=bool(agent_meta.get("pinned", False)),
        uses=int(usage.get("uses", 0)),
        last_used_at=float(usage.get("last_used_at") or agent_meta.get("created_at") or 0.0),
        created_at=float(agent_meta.get("created_at") or 0.0),
    )


def discover() -> list[AgentSkill]:
    """Todas las skills. Si un nombre se repite: instalada > incluida > del agente."""
    state = _load_state()
    disabled = set(state.get("disabled", []))
    found: dict[str, AgentSkill] = {}
    sources = (
        (AGENT_DIR, "*/SKILL.md", "agent"),
        (BUNDLED_DIR, "*/SKILL.md", "bundled"),
        (INSTALLED_DIR, "*/*/SKILL.md", "installed"),
    )
    for base, pattern, source in sources:
        if not base.is_dir():
            continue
        for skill_md in sorted(base.glob(pattern)):
            pack = skill_md.parent.parent.name if source == "installed" else ""
            skill = _load_skill(skill_md.parent, source, pack, disabled, state)
            if skill:
                found[skill.name] = skill
    return sorted(found.values(), key=lambda s: s.name)


def get_skill(name: str) -> AgentSkill | None:
    name = str(name or "").strip().lower()
    for skill in discover():
        if skill.name == name:
            return skill
    return None


def read_skill(name: str, *, mark_use: bool = True) -> dict[str, Any]:
    """Cuerpo completo de la skill + lista de recursos (para la acción skill_read)."""
    skill = get_skill(name)
    if not skill:
        raise SkillError(f"No existe la skill '{name}'.")
    if not skill.enabled:
        raise SkillError(f"La skill '{name}' está desactivada.")
    text = (Path(skill.path) / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    _meta, body = parse_skill_md(text)
    if mark_use:
        mark_used(skill)
    return {
        "name": skill.name,
        "description": skill.description,
        "instructions": body,
        "resources": skill.resources,
        "source": skill.source,
    }


def mark_used(skill: AgentSkill) -> None:
    """Uso real de una skill: cuenta para el curator y reactiva una skill del agente."""
    def mutate(state: dict[str, Any]) -> None:
        entry = state["usage"].setdefault(skill.name, {"uses": 0})
        entry["uses"] = int(entry.get("uses", 0)) + 1
        entry["last_used_at"] = time.time()
        meta = state["agent"].get(skill.name)
        if skill.source == "agent" and meta and meta.get("lifecycle") != "active":
            meta["lifecycle"] = "active"
            logger.info(f"agent_skills: '{skill.name}' vuelve a estar activa")

    _update_state(mutate)


def read_resource(name: str, relative_path: str) -> str:
    """Lee un archivo de references/, scripts/, etc. de la skill (texto, con tope)."""
    skill = get_skill(name)
    if not skill:
        raise SkillError(f"No existe la skill '{name}'.")
    base = Path(skill.path).resolve()
    target = (base / str(relative_path or "")).resolve()
    if base not in target.parents and target != base:
        raise SkillError("Ruta fuera de la carpeta de la skill.")
    if not target.is_file():
        raise SkillError(f"No existe el recurso '{relative_path}'.")
    data = target.read_bytes()
    truncated = len(data) > MAX_RESOURCE_BYTES
    text = data[:MAX_RESOURCE_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[... recortado: {len(data)} bytes en total]"
    return text


def set_enabled(name: str, enabled: bool) -> AgentSkill:
    skill = get_skill(name)
    if not skill:
        raise SkillError(f"No existe la skill '{name}'.")
    def mutate(state: dict[str, Any]) -> None:
        disabled = set(state.get("disabled", []))
        if enabled:
            disabled.discard(skill.name)
        else:
            disabled.add(skill.name)
        state["disabled"] = sorted(disabled)

    _update_state(mutate)
    skill.enabled = enabled
    return skill


# ── Índice para el prompt ────────────────────────────────────────────────

def build_prompt_index(max_chars: int = 6000) -> str:
    """Bloque para el system prompt: nombre + descripción de las skills activas."""
    skills = [s for s in discover() if s.enabled and s.lifecycle != "archived"]
    if not skills:
        return ""
    lines = [
        "## Skills disponibles (instrucciones especializadas)",
        "Antes de una tarea que calce con alguna, carga sus instrucciones con "
        '[ACTION:skill_read(name="<nombre>")] y síguelas. Para un archivo de apoyo: '
        '[ACTION:skill_resource(name="<nombre>", path="<ruta>")]. No las cargues si no aplican.',
    ]
    used = sum(len(line) for line in lines)
    for skill in skills:
        mine = " (la escribiste tú)" if skill.source == "agent" else ""
        line = f"- {skill.name}{mine}: {skill.description}"
        if used + len(line) > max_chars:
            lines.append(f"- (+{len(skills) - (len(lines) - 2)} skills más; pide la lista con skill_list)")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


# ── Instalación ──────────────────────────────────────────────────────────

def _tmp_root() -> Path:
    """Temporales junto a las skills (mismo disco: move atómico, no llena %TEMP%)."""
    path = SKILLS_DIR / ".tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_pack_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", str(value or "").lower()).strip("-")
    return slug[:48] or f"pack-{int(time.time())}"


def _find_skill_dirs(root: Path) -> list[Path]:
    dirs = []
    for skill_md in sorted(root.rglob("SKILL.md")):
        rel_parts = skill_md.relative_to(root).parts
        if any(part.startswith(".") or part in ("node_modules", "__pycache__") for part in rel_parts):
            continue
        dirs.append(skill_md.parent)
    return dirs[:MAX_SKILLS_PER_IMPORT]


def _copy_skill_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules", "*.pyc", ".DS_Store"),
    )


def install_from_folder(folder: str | Path, pack: str | None = None, source_url: str = "") -> dict[str, Any]:
    """Copia todas las carpetas con SKILL.md encontradas bajo `folder`."""
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise SkillError(f"No existe la carpeta {root}.")
    skill_dirs = _find_skill_dirs(root)
    if not skill_dirs:
        raise SkillError("No se encontró ningún SKILL.md en esa ubicación.")
    pack_name = _safe_pack_name(pack or root.name)
    pack_dir = INSTALLED_DIR / pack_name
    installed: list[str] = []
    skipped: list[dict[str, str]] = []
    staging = Path(tempfile.mkdtemp(prefix="staging-", dir=_tmp_root()))
    try:
        for src in skill_dirs:
            try:
                meta, _body = parse_skill_md((src / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
                name, _desc = validate_meta(meta, src.name)
            except SkillError as exc:
                skipped.append({"path": str(src.relative_to(root)), "reason": str(exc)})
                continue
            if name in installed:
                skipped.append({"path": str(src.relative_to(root)), "reason": f"nombre duplicado '{name}'"})
                continue
            _copy_skill_tree(src, staging / name)
            installed.append(name)
        if not installed:
            raise SkillError("Ninguna skill válida para instalar: " + "; ".join(s["reason"] for s in skipped[:5]))
        if pack_dir.exists():
            shutil.rmtree(pack_dir)
        pack_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(pack_dir))
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    _update_state(lambda state: state["packs"].__setitem__(pack_name, {
        "source": source_url or str(root),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "skills": installed,
    }))
    logger.info(f"agent_skills: pack '{pack_name}' instalado con {len(installed)} skills")
    return {"pack": pack_name, "installed": installed, "skipped": skipped}


def parse_github_source(source: str) -> tuple[str, str, str | None, str]:
    """
    Acepta owner/repo, https://github.com/owner/repo, .../tree/<ref>/<subpath>.
    Devuelve (owner, repo, ref|None, subpath).
    """
    text = str(source or "").strip()
    if not text:
        raise SkillError("Indica un repositorio de GitHub.")
    if re.fullmatch(r"[\w.-]+/[\w.-]+", text):
        owner, repo = text.split("/", 1)
        return owner, repo.removesuffix(".git"), None, ""
    parsed = urlparse(text)
    if parsed.scheme not in ("https", "http") or parsed.netloc.lower() not in ("github.com", "www.github.com"):
        raise SkillError("Solo se admiten repositorios de github.com.")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise SkillError("URL de GitHub incompleta (falta owner/repo).")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    ref = None
    subpath = ""
    if len(parts) >= 4 and parts[2] in ("tree", "blob"):
        ref = parts[3]
        subpath = "/".join(parts[4:])
        if subpath.endswith("SKILL.md"):
            subpath = subpath.rsplit("/", 1)[0] if "/" in subpath else ""
    return owner, repo, ref, subpath


def _extract_zip_safely(data: bytes, dest: Path) -> Path:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillError("La descarga no es un zip válido.") from exc
    with archive as zf:
        total = 0
        for info in zf.infolist():
            total += info.file_size
            if total > MAX_ARCHIVE_BYTES * 3:
                raise SkillError("El repositorio es demasiado grande para importar skills.")
            target = (dest / info.filename).resolve()
            if dest.resolve() not in target.parents and target != dest.resolve():
                raise SkillError("Archivo con ruta insegura dentro del zip.")
        zf.extractall(dest)
    children = [p for p in dest.iterdir() if p.is_dir()]
    return children[0] if len(children) == 1 else dest


async def install_from_github(source: str, pack: str | None = None, timeout: float = 60.0) -> dict[str, Any]:
    """Descarga el zip del repo (rama por defecto o ref) e instala sus skills."""
    import aiohttp

    owner, repo, ref, subpath = parse_github_source(source)
    url = f"https://api.github.com/repos/{owner}/{repo}/zipball" + (f"/{ref}" if ref else "")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "G-Mini-Agent"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.get(url, headers=headers, allow_redirects=True) as resp:
            if resp.status == 404:
                raise SkillError(f"No se encontró {owner}/{repo} (¿es público?).")
            if resp.status >= 400:
                raise SkillError(f"GitHub respondió {resp.status} al descargar {owner}/{repo}.")
            length = int(resp.headers.get("Content-Length") or 0)
            if length > MAX_ARCHIVE_BYTES:
                raise SkillError("El repositorio es demasiado grande para importar skills.")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.content.iter_chunked(64 * 1024):
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise SkillError("El repositorio es demasiado grande para importar skills.")
                chunks.append(chunk)
            data = b"".join(chunks)

    tmp = Path(tempfile.mkdtemp(prefix="zip-", dir=_tmp_root()))
    try:
        root = _extract_zip_safely(data, tmp)
        base = (root / subpath).resolve() if subpath else root
        if not base.is_dir() or (root.resolve() not in base.parents and base != root.resolve()):
            raise SkillError(f"La ruta '{subpath}' no existe en el repositorio.")
        source_url = f"https://github.com/{owner}/{repo}" + (f"/tree/{ref}/{subpath}" if ref else "")
        return install_from_folder(base, pack=pack or repo, source_url=source_url)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def remove_pack(pack: str) -> bool:
    pack_name = _safe_pack_name(pack)
    pack_dir = INSTALLED_DIR / pack_name
    if not pack_dir.is_dir():
        return False
    shutil.rmtree(pack_dir)
    _update_state(lambda state: state["packs"].pop(pack_name, None))
    return True


def list_packs() -> list[dict[str, Any]]:
    state = _load_state()
    return [{"pack": name, **info} for name, info in sorted(state.get("packs", {}).items())]


# ── Skills escritas por el agente (modo aprendiz) ────────────────────────

def _agent_file_path(skill_dir: Path, relative: str) -> Path:
    """Ruta de un archivo de apoyo: solo dentro de references/, scripts/, etc."""
    candidate = Path(str(relative or "").replace("\\", "/"))
    parts = candidate.parts
    if not parts or candidate.is_absolute() or candidate.drive or ".." in parts:
        raise SkillError(f"Ruta no permitida: {relative!r}")
    if parts[0] not in RESOURCE_DIRS:
        raise SkillError(f"Los archivos van en {', '.join(RESOURCE_DIRS)}/ (recibí {relative!r}).")
    if candidate.suffix.lower() not in AGENT_FILE_SUFFIXES:
        raise SkillError(f"Extensión no permitida: {relative!r}")
    target = (skill_dir / candidate).resolve()
    if skill_dir.resolve() not in target.parents:
        raise SkillError(f"Ruta fuera de la skill: {relative!r}")
    return target


def author_skill(
    name: str,
    description: str,
    instructions: str,
    files: dict[str, str] | None = None,
    *,
    overwrite: bool = False,
) -> AgentSkill:
    """Crea (o reemplaza) una skill del agente. Valida todo antes de escribir."""
    name = str(name or "").strip().lower()
    meta = {"name": name, "description": " ".join(str(description or "").split())}
    validate_meta(meta)
    body = str(instructions or "").strip()
    if len(body) < 40:
        raise SkillError("Las instrucciones son demasiado cortas para ser útiles.")
    files = dict(files or {})
    if len(files) > MAX_AGENT_FILES:
        raise SkillError(f"Demasiados archivos (máx. {MAX_AGENT_FILES}).")

    existing = get_skill(name)
    if existing and existing.source != "agent":
        raise SkillError(f"Ya existe una skill '{name}' instalada o incluida; usa otro nombre.")
    if existing and not overwrite:
        raise SkillError(f"La skill '{name}' ya existe: usa overwrite=true para reemplazarla.")
    if existing and existing.pinned:
        raise SkillError(f"La skill '{name}' está fijada por el usuario y no se puede reemplazar.")

    skill_dir = AGENT_DIR / name
    planned = [(_agent_file_path(skill_dir, rel), str(content)) for rel, content in files.items()]
    for _target, content in planned:
        if len(content.encode("utf-8")) > MAX_RESOURCE_BYTES:
            raise SkillError("Un archivo de apoyo supera el tamaño máximo.")
    frontmatter = yaml.safe_dump(
        {**meta, "metadata": {"author": "agent", "created": time.strftime("%Y-%m-%d")}},
        allow_unicode=True, sort_keys=False,
    ).strip()
    document = f"---\n{frontmatter}\n---\n\n{body}\n"
    if len(document.encode("utf-8")) > MAX_SKILL_BYTES:
        raise SkillError("SKILL.md demasiado grande.")

    staging = Path(tempfile.mkdtemp(prefix="author-", dir=_tmp_root()))
    try:
        (staging / "SKILL.md").write_text(document, encoding="utf-8")
        for target, content in planned:
            dest = staging / target.relative_to(skill_dir.resolve())
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
        skill_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(skill_dir))
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    def mutate(state: dict[str, Any]) -> None:
        previous = state["agent"].get(name, {})
        state["agent"][name] = {
            "created_at": previous.get("created_at") or time.time(),
            "updated_at": time.time(),
            "lifecycle": "active",
            "pinned": False,
        }

    _update_state(mutate)
    logger.info(f"agent_skills: skill del agente '{name}' {'actualizada' if existing else 'creada'}")
    created = get_skill(name)
    assert created is not None
    return created


def set_pinned(name: str, pinned: bool) -> AgentSkill:
    skill = get_skill(name)
    if not skill or skill.source != "agent":
        raise SkillError(f"'{name}' no es una skill del agente.")
    _update_state(lambda state: state["agent"].setdefault(skill.name, {}).__setitem__("pinned", bool(pinned)))
    skill.pinned = bool(pinned)
    return skill


def set_lifecycle(name: str, lifecycle: str) -> None:
    if lifecycle not in LIFECYCLES:
        raise SkillError(f"Estado inválido: {lifecycle}")
    _update_state(lambda state: state["agent"].setdefault(name, {}).__setitem__("lifecycle", lifecycle))


def delete_agent_skill(name: str) -> bool:
    """Solo desde la UI: el usuario borra una skill que escribió el agente."""
    skill = get_skill(name)
    if not skill or skill.source != "agent":
        return False
    shutil.rmtree(skill.path, ignore_errors=True)
    _update_state(lambda state: state["agent"].pop(skill.name, None))
    return True
