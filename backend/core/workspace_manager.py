"""
Structured local workspace and file operations for G-Mini Agent.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterator

from loguru import logger

from backend.config import ROOT_DIR, config


PROJECT_MARKERS = [
    ".git",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "composer.json",
    "pom.xml",
    "*.sln",
]

# Rutas del sistema que NUNCA deben ser accesibles para escritura
_SYSTEM_DENY_PATTERNS_WRITE: tuple[str, ...] = (
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData",
    "/usr",
    "/bin",
    "/sbin",
    "/etc",
    "/var",
    "/boot",
    "/lib",
    "/proc",
    "/sys",
)


# Archivos de credenciales que el agente nunca lee: una inyección de prompt
# (web, documento, mensaje) podría pedirle leerlos y mandarlos fuera.
_CREDENTIAL_DIR_PARTS: frozenset[str] = frozenset({".ssh", ".gnupg", ".aws", ".azure", ".kube", ".password-store"})
_CREDENTIAL_FILE_NAMES: frozenset[str] = frozenset({
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", "_netrc", ".git-credentials",
    "login data", "login data for account", "cookies", "local state", "key4.db", "logins.json",
    "application_default_credentials.json", "credentials.db", "access_tokens.db",
})
_CREDENTIAL_PATH_FRAGMENTS: tuple[str, ...] = (
    "/appdata/roaming/gcloud/",
    "/.config/gcloud/",
    "/appdata/roaming/microsoft/credentials/",
    "/appdata/roaming/microsoft/protect/",
    "/appdata/local/microsoft/credentials/",
    "/data/runtime/",
)


def credential_read_reason(resolved: Path) -> str | None:
    """Motivo si la ruta es un almacén de credenciales; None si se puede leer."""
    normalized = "/" + str(resolved).replace("\\", "/").lower().lstrip("/") + "/"
    if any(fragment in normalized for fragment in _CREDENTIAL_PATH_FRAGMENTS):
        return "almacén de credenciales protegido"
    parts = {part.lower() for part in resolved.parts}
    if parts & _CREDENTIAL_DIR_PARTS:
        return "carpeta de credenciales protegida"
    if resolved.name.lower() in _CREDENTIAL_FILE_NAMES:
        return "archivo de credenciales protegido"
    return None


def self_write_reason(resolved: Path) -> str | None:
    """Motivo si la escritura toca la config, el código o las skills ejecutables de G-Mini.

    Con acceso de escritura a esas rutas, un texto inyectado (una web, un
    archivo) podría agregar un hook o una skill que luego corre sin aprobación,
    o cambiar la propia policy. Esas rutas se cambian desde la UI o con
    skill_author (que siempre pide aprobación). security.protect_self: false lo
    desactiva para quien desarrolla G-Mini con G-Mini.
    """
    from backend.config import CODE_DIR, ROOT_DIR, config

    if not config.get("security", "protect_self", default=True):
        return None
    protected_files = {
        (ROOT_DIR / "config.user.yaml").resolve(),
        (CODE_DIR / "config.default.yaml").resolve(),
    }
    protected_dirs = (
        ROOT_DIR / "data" / "runtime", ROOT_DIR / "data" / "skills", ROOT_DIR / "data" / "agent_skills",
        CODE_DIR / "backend", CODE_DIR / "electron",
    )
    if resolved in protected_files:
        return "configuración de G-Mini protegida (cámbiala desde Ajustes)"
    for folder in protected_dirs:
        try:
            resolved.relative_to(folder.resolve())
            return "archivos internos de G-Mini protegidos"
        except ValueError:
            continue
    return None


_GIT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@{}~^:+-]{0,199}$")


def _validate_git_ref(ref: str) -> str:
    """Un ref nunca puede empezar con '-' (sería una opción de git, p. ej. --output=)."""
    value = str(ref).strip()
    if not _GIT_REF_RE.match(value):
        raise ValueError(f"ref de git inválido: {value[:60]!r}")
    return value


class PathAccessDeniedError(PermissionError):
    """Raised when a file operation targets a path outside the allowed sandbox."""

    def __init__(self, path: Path, reason: str) -> None:
        self.denied_path = path
        self.reason = reason
        super().__init__(f"Acceso denegado a {path}: {reason}")


class WorkspaceManager:
    """
    Provides safe, structured file operations for local workspaces.
    Relative paths resolve against the project root by default.
    Write operations are sandboxed to allowed directories.
    """

    def __init__(
        self,
        root_dir: Path | None = None,
        *,
        max_read_chars: int = 20_000,
        max_search_results: int = 100,
        max_search_file_bytes: int = 1_000_000,
    ) -> None:
        self._root_dir = Path(root_dir or ROOT_DIR).resolve()
        self._max_read_chars = max_read_chars
        self._max_search_results = max_search_results
        self._max_search_file_bytes = max_search_file_bytes
        self._allowed_write_dirs: list[Path] = self._build_allowed_write_dirs()

    def _build_allowed_write_dirs(self) -> list[Path]:
        """Build the list of directories where write operations are allowed."""
        home = Path.home().resolve()
        defaults = [
            self._root_dir,
            home / "Desktop",
            home / "Documents",
            home / "Downloads",
            home / "Escritorio",      # Spanish locale
            home / "Documentos",
            home / "Descargas",
            home / ".gmini",
        ]
        extra_raw: list[str] = config.get("security", "allowed_write_dirs", default=[]) or []
        for raw in extra_raw:
            try:
                expanded = os.path.expandvars(str(raw).strip())
                p = Path(expanded).expanduser().resolve()
                if p.is_absolute():
                    defaults.append(p)
            except (OSError, ValueError):
                continue
        return [d for d in defaults if d.is_absolute()]

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    def resolve_path(self, raw_path: str | None = None) -> Path:
        text = str(raw_path or "").strip().strip("\"'")
        if not text:
            return self._root_dir
        if text.startswith("$HOME"):
            text = text.replace("$HOME", str(Path.home()), 1)
        elif text.startswith("~"):
            text = str(Path.home()) + text[1:]
        text = os.path.expandvars(text)
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = self._root_dir / path
        return path.resolve()

    def _check_read_access(self, resolved: Path) -> None:
        reason = credential_read_reason(resolved)
        if reason:
            logger.warning(f"Lectura bloqueada ({reason}): {resolved}")
            raise PathAccessDeniedError(resolved, reason)

    def _check_write_access(self, resolved: Path) -> None:
        """Validate that a resolved path is within an allowed write directory."""
        reason = self_write_reason(resolved) or credential_read_reason(resolved)
        if reason:
            logger.warning(f"Escritura bloqueada ({reason}): {resolved}")
            raise PathAccessDeniedError(resolved, reason)
        resolved_str = str(resolved)
        for deny in _SYSTEM_DENY_PATTERNS_WRITE:
            if resolved_str.lower().startswith(deny.lower()):
                logger.warning(f"Write blocked to system path: {resolved}")
                raise PathAccessDeniedError(
                    resolved,
                    f"ruta del sistema protegida ({deny})"
                )
        for allowed in self._allowed_write_dirs:
            try:
                resolved.relative_to(allowed)
                return
            except ValueError:
                continue
        logger.warning(f"Write blocked outside sandbox: {resolved}")
        raise PathAccessDeniedError(
            resolved,
            "ruta fuera de los directorios permitidos para escritura. "
            "Directorios permitidos: " + ", ".join(str(d) for d in self._allowed_write_dirs)
        )

    def list_files(
        self,
        path: str | None = None,
        *,
        pattern: str = "*",
        recursive: bool = False,
        include_hidden: bool = False,
        include_dirs: bool = False,
        max_results: int = 200,
    ) -> dict[str, Any]:
        base_path = self.resolve_path(path)
        if not base_path.exists():
            raise FileNotFoundError(f"ruta no encontrada: {base_path}")

        if base_path.is_file():
            entries = [self._build_entry(base_path)]
            return {
                "base_path": str(base_path),
                "entries": entries,
                "count": len(entries),
                "truncated": False,
            }

        iterator = base_path.rglob(pattern) if recursive else base_path.glob(pattern)
        entries: list[dict[str, Any]] = []
        truncated = False
        limit = max(1, int(max_results))

        for candidate in iterator:
            if not include_hidden and self._is_hidden(candidate, base_path):
                continue
            if candidate.is_dir() and not include_dirs:
                continue
            entries.append(self._build_entry(candidate))
            if len(entries) >= limit:
                truncated = True
                break

        return {
            "base_path": str(base_path),
            "entries": entries,
            "count": len(entries),
            "truncated": truncated,
        }

    def read_text_file(
        self,
        path: str,
        *,
        start_line: int = 1,
        max_lines: int = 200,
        max_chars: int | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        resolved = self.resolve_path(path)
        self._check_read_access(resolved)
        if not resolved.exists():
            raise FileNotFoundError(f"archivo no encontrado: {resolved}")
        if not resolved.is_file():
            raise IsADirectoryError(f"la ruta es un directorio: {resolved}")

        content = self._read_text_lossy(resolved, encoding=encoding)
        lines = content.splitlines()
        total_lines = len(lines)

        safe_start = max(1, int(start_line))
        safe_max_lines = max(1, int(max_lines))
        start_index = safe_start - 1
        end_index = min(total_lines, start_index + safe_max_lines)

        excerpt = "\n".join(lines[start_index:end_index])
        char_limit = max_chars if max_chars is not None else self._max_read_chars
        truncated = False
        if len(excerpt) > char_limit:
            excerpt = excerpt[:char_limit]
            truncated = True
        if end_index < total_lines:
            truncated = True

        return {
            "path": str(resolved),
            "relative_path": self._relative_path(resolved),
            "content": excerpt,
            "start_line": safe_start,
            "end_line": end_index,
            "total_lines": total_lines,
            "truncated": truncated,
            "encoding": encoding,
        }

    def read_text_file_tail(
        self,
        path: str,
        *,
        max_chars: int = 20000,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        resolved = self.resolve_path(path)
        self._check_read_access(resolved)
        if not resolved.exists():
            raise FileNotFoundError(f"archivo no encontrado: {resolved}")
        if not resolved.is_file():
            raise IsADirectoryError(f"la ruta es un directorio: {resolved}")

        safe_max_chars = max(1, int(max_chars))
        with resolved.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            total_bytes = handle.tell()
            byte_window = max(safe_max_chars * 4, 4096)
            read_from = max(0, total_bytes - byte_window)
            handle.seek(read_from, os.SEEK_SET)
            raw = handle.read()

        excerpt = raw.decode(encoding, errors="replace")
        if len(excerpt) > safe_max_chars:
            excerpt = excerpt[-safe_max_chars:]
        truncated = total_bytes > len(raw)

        return {
            "path": str(resolved),
            "relative_path": self._relative_path(resolved),
            "content": excerpt,
            "max_chars": safe_max_chars,
            "truncated": truncated,
            "encoding": encoding,
            "read_mode": "tail",
            "total_bytes": total_bytes,
        }

    def search_text(
        self,
        query: str,
        *,
        path: str | None = None,
        pattern: str = "*",
        recursive: bool = True,
        case_sensitive: bool = False,
        max_results: int | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        needle = str(query or "")
        if not needle:
            raise ValueError("query vacia")

        base_path = self.resolve_path(path)
        if not base_path.exists():
            raise FileNotFoundError(f"ruta no encontrada: {base_path}")

        limit = max_results if max_results is not None else self._max_search_results
        limit = max(1, int(limit))
        files_scanned = 0
        files_skipped = 0
        matches: list[dict[str, Any]] = []
        query_cmp = needle if case_sensitive else needle.lower()

        for candidate in self._iter_search_files(base_path, pattern=pattern, recursive=recursive):
            if credential_read_reason(candidate):
                files_skipped += 1
                continue
            try:
                if candidate.stat().st_size > self._max_search_file_bytes:
                    files_skipped += 1
                    continue
                content = self._read_text_lossy(candidate, encoding=encoding)
            except (OSError, ValueError):
                files_skipped += 1
                continue

            files_scanned += 1
            for line_number, line in enumerate(content.splitlines(), start=1):
                line_cmp = line if case_sensitive else line.lower()
                column = line_cmp.find(query_cmp)
                if column < 0:
                    continue
                matches.append(
                    {
                        "path": str(candidate),
                        "relative_path": self._relative_path(candidate),
                        "line": line_number,
                        "column": column + 1,
                        "line_text": line[:500],
                    }
                )
                if len(matches) >= limit:
                    return {
                        "base_path": str(base_path),
                        "query": needle,
                        "matches": matches,
                        "count": len(matches),
                        "files_scanned": files_scanned,
                        "files_skipped": files_skipped,
                        "truncated": True,
                    }

        return {
            "base_path": str(base_path),
            "query": needle,
            "matches": matches,
            "count": len(matches),
            "files_scanned": files_scanned,
            "files_skipped": files_skipped,
            "truncated": False,
        }

    def read_text_files(
        self,
        paths: list[str],
        *,
        start_line: int = 1,
        max_lines: int = 200,
        max_chars_per_file: int | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for raw_path in paths:
            item = self.read_text_file(
                raw_path,
                start_line=start_line,
                max_lines=max_lines,
                max_chars=max_chars_per_file,
                encoding=encoding,
            )
            items.append(item)
        return {
            "count": len(items),
            "files": items,
        }

    def replace_text(
        self,
        path: str,
        *,
        find: str,
        replace: str,
        count: int = 1,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        resolved = self.resolve_path(path)
        self._check_write_access(resolved)
        if not resolved.exists():
            raise FileNotFoundError(f"archivo no encontrado: {resolved}")
        if not resolved.is_file():
            raise IsADirectoryError(f"la ruta es un directorio: {resolved}")
        if not find:
            raise ValueError("find vacio")

        content = self._read_text_lossy(resolved, encoding=encoding)
        occurrences = content.count(find)
        if occurrences == 0:
            return {
                "path": str(resolved),
                "relative_path": self._relative_path(resolved),
                "replaced_count": 0,
                "occurrences_found": 0,
                "changed": False,
                "encoding": encoding,
            }

        replace_count = int(count)
        if replace_count <= 0:
            updated = content.replace(find, replace)
            replaced_count = occurrences
        else:
            updated = content.replace(find, replace, replace_count)
            replaced_count = min(occurrences, replace_count)

        resolved.write_text(updated, encoding=encoding)
        return {
            "path": str(resolved),
            "relative_path": self._relative_path(resolved),
            "replaced_count": replaced_count,
            "occurrences_found": occurrences,
            "changed": replaced_count > 0,
            "encoding": encoding,
        }

    def write_text_file(
        self,
        path: str,
        text: str,
        *,
        append: bool = False,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        resolved = self.resolve_path(path)
        self._check_write_access(resolved)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if append:
            with resolved.open("a", encoding=encoding) as handle:
                handle.write(text)
        else:
            resolved.write_text(text, encoding=encoding)
        stat = resolved.stat()
        return {
            "path": str(resolved),
            "relative_path": self._relative_path(resolved),
            "exists": True,
            "size_bytes": stat.st_size,
            "encoding": encoding,
            "append": append,
        }

    def file_exists(self, path: str) -> dict[str, Any]:
        resolved = self.resolve_path(path)
        exists = resolved.exists()
        data: dict[str, Any] = {
            "path": str(resolved),
            "relative_path": self._relative_path(resolved),
            "exists": exists,
        }
        if exists:
            stat = resolved.stat()
            data["size_bytes"] = stat.st_size
            data["modified_ts"] = stat.st_mtime
            data["is_dir"] = resolved.is_dir()
        return data

    def find_project_root(self, path: str | None = None) -> dict[str, Any]:
        resolved = self.resolve_path(path)
        start = resolved if resolved.is_dir() else resolved.parent

        for candidate in [start, *start.parents]:
            markers = self._collect_project_markers(candidate)
            if markers:
                return {
                    "path": str(candidate),
                    "relative_path": self._relative_path(candidate),
                    "markers": markers,
                    "detected_kinds": self._detect_project_kinds(markers),
                }

        markers = self._collect_project_markers(self._root_dir)
        return {
            "path": str(self._root_dir),
            "relative_path": ".",
            "markers": markers,
            "detected_kinds": self._detect_project_kinds(markers),
        }

    def git_status(self, path: str | None = None, *, max_entries: int = 100) -> dict[str, Any]:
        git_path = shutil.which("git")
        if not git_path:
            return {
                "available": False,
                "is_repo": False,
                "reason": "git no disponible en PATH",
            }

        repo_resolution = self._resolve_git_repo(path)
        if not repo_resolution["is_repo"]:
            return repo_resolution

        repo_root = Path(str(repo_resolution["repo_root"]))
        branch_data = self._run_git(repo_root, ["status", "--short", "--branch"])
        lines = [line for line in (branch_data["stdout"] or "").splitlines() if line.strip()]
        branch = lines[0] if lines else ""
        entries: list[dict[str, Any]] = []
        truncated = False
        for line in lines[1:]:
            if len(entries) >= max(1, int(max_entries)):
                truncated = True
                break
            status = line[:2]
            file_path = line[3:].strip() if len(line) > 3 else ""
            entries.append(
                {
                    "status": status,
                    "path": file_path,
                }
            )

        return {
            "available": True,
            "is_repo": True,
            "repo_root": str(repo_root),
            "relative_repo_root": self._relative_path(repo_root),
            "branch": branch,
            "entries": entries,
            "count": len(entries),
            "truncated": truncated,
        }

    def git_changed_files(
        self,
        path: str | None = None,
        *,
        staged: bool = False,
        max_entries: int = 100,
    ) -> dict[str, Any]:
        status = self.git_status(path=path, max_entries=max_entries)
        if not status.get("is_repo"):
            return status

        entries = list(status.get("entries", []))
        if staged:
            filtered: list[dict[str, Any]] = []
            for entry in entries:
                status_code = str(entry.get("status", "  "))
                index_status = status_code[0] if status_code else " "
                if index_status not in {" ", "?"}:
                    filtered.append(entry)
            entries = filtered
        return {
            **status,
            "entries": entries[: max(1, int(max_entries))],
            "count": len(entries[: max(1, int(max_entries))]),
            "staged": staged,
        }

    def git_diff(
        self,
        path: str | None = None,
        *,
        staged: bool = False,
        ref: str | None = None,
        max_chars: int = 20_000,
    ) -> dict[str, Any]:
        repo_resolution = self._resolve_git_repo(path)
        if not repo_resolution["is_repo"]:
            return repo_resolution

        repo_root = Path(str(repo_resolution["repo_root"]))
        relative_target = self._resolve_relative_git_target(path, repo_root)

        args = ["diff"]
        if staged:
            args.append("--cached")
        if ref:
            args.append(_validate_git_ref(ref))
        if relative_target:
            args.extend(["--", relative_target])

        diff_result = self._run_git(repo_root, args)
        content = diff_result["stdout"] or ""
        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars]

        return {
            "available": True,
            "is_repo": True,
            "repo_root": str(repo_root),
            "relative_repo_root": self._relative_path(repo_root),
            "path": relative_target,
            "staged": staged,
            "ref": ref or "",
            "content": content,
            "truncated": truncated,
            "return_code": diff_result["return_code"],
        }

    def git_log(
        self,
        path: str | None = None,
        *,
        limit: int = 10,
    ) -> dict[str, Any]:
        repo_resolution = self._resolve_git_repo(path)
        if not repo_resolution["is_repo"]:
            return repo_resolution

        repo_root = Path(str(repo_resolution["repo_root"]))
        relative_target = self._resolve_relative_git_target(path, repo_root)
        safe_limit = max(1, int(limit))
        args = [
            "log",
            f"-n{safe_limit}",
            "--date=short",
            "--pretty=format:%H%x1f%h%x1f%ad%x1f%an%x1f%s",
        ]
        if relative_target:
            args.extend(["--", relative_target])

        log_result = self._run_git(repo_root, args)
        entries: list[dict[str, Any]] = []
        for line in (log_result["stdout"] or "").splitlines():
            parts = line.split("\x1f")
            if len(parts) != 5:
                continue
            entries.append(
                {
                    "commit": parts[0],
                    "short_commit": parts[1],
                    "date": parts[2],
                    "author": parts[3],
                    "subject": parts[4],
                }
            )

        return {
            "available": True,
            "is_repo": True,
            "repo_root": str(repo_root),
            "relative_repo_root": self._relative_path(repo_root),
            "path": relative_target,
            "entries": entries,
            "count": len(entries),
            "return_code": log_result["return_code"],
        }

    def code_outline(self, path: str, *, max_symbols: int = 200) -> dict[str, Any]:
        resolved = self.resolve_path(path)
        self._check_read_access(resolved)
        if not resolved.exists():
            raise FileNotFoundError(f"archivo no encontrado: {resolved}")
        if not resolved.is_file():
            raise IsADirectoryError(f"la ruta es un directorio: {resolved}")

        content = self._read_text_lossy(resolved, encoding="utf-8")
        language = self._detect_language(resolved)
        if language == "python":
            symbols = self._python_outline(content, max_symbols=max_symbols)
        elif language in {"javascript", "typescript"}:
            symbols = self._js_ts_outline(content, max_symbols=max_symbols)
        else:
            symbols = self._generic_outline(content, max_symbols=max_symbols)

        return {
            "path": str(resolved),
            "relative_path": self._relative_path(resolved),
            "language": language,
            "symbols": symbols,
            "count": len(symbols),
        }

    def code_related_files(
        self,
        path: str,
        *,
        max_results: int = 20,
    ) -> dict[str, Any]:
        resolved = self.resolve_path(path)
        self._check_read_access(resolved)
        if not resolved.exists():
            raise FileNotFoundError(f"archivo no encontrado: {resolved}")
        if not resolved.is_file():
            raise IsADirectoryError(f"la ruta es un directorio: {resolved}")

        language = self._detect_language(resolved)
        content = self._read_text_lossy(resolved, encoding="utf-8")
        related: list[dict[str, Any]] = []
        seen: set[str] = set()

        for item in self._find_sibling_related_files(resolved):
            key = str(item["path"]).lower()
            if key not in seen:
                seen.add(key)
                related.append(item)
            if len(related) >= max_results:
                break

        import_paths = self._extract_import_paths(resolved, content, language)
        for candidate_path, reason in import_paths:
            normalized_key = str(candidate_path).lower()
            if normalized_key in seen or not candidate_path.exists() or not candidate_path.is_file():
                continue
            seen.add(normalized_key)
            related.append(
                {
                    "path": str(candidate_path),
                    "relative_path": self._relative_path(candidate_path),
                    "reason": reason,
                }
            )
            if len(related) >= max_results:
                break

        return {
            "path": str(resolved),
            "relative_path": self._relative_path(resolved),
            "language": language,
            "related_files": related,
            "count": len(related),
        }

    def workspace_snapshot(
        self,
        path: str | None = None,
        *,
        max_entries: int = 80,
        include_git: bool = True,
    ) -> dict[str, Any]:
        root_info = self.find_project_root(path)
        project_root = Path(root_info["path"])
        listing = self.list_files(
            str(project_root),
            pattern="*",
            recursive=False,
            include_hidden=False,
            include_dirs=True,
            max_results=max_entries,
        )
        manifests = self._collect_project_markers(project_root)
        snapshot: dict[str, Any] = {
            "project_root": str(project_root),
            "relative_project_root": self._relative_path(project_root),
            "markers": manifests,
            "detected_kinds": self._detect_project_kinds(manifests),
            "entries": listing["entries"],
            "entry_count": listing["count"],
            "entries_truncated": listing["truncated"],
        }
        if include_git:
            snapshot["git"] = self.git_status(str(project_root))
        return snapshot

    def _iter_search_files(self, base_path: Path, *, pattern: str, recursive: bool) -> Iterator[Path]:
        if base_path.is_file():
            yield base_path
            return

        iterator = base_path.rglob(pattern) if recursive else base_path.glob(pattern)
        for candidate in iterator:
            if candidate.is_file() and not self._is_hidden(candidate, base_path):
                yield candidate

    def _build_entry(self, path: Path) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "name": path.name,
            "path": str(path),
            "relative_path": self._relative_path(path),
            "is_dir": path.is_dir(),
        }
        if path.is_file():
            try:
                entry["size_bytes"] = path.stat().st_size
            except OSError:
                entry["size_bytes"] = None
        return entry

    def _relative_path(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self._root_dir))
        except ValueError:
            return str(path.resolve())

    def _read_text_lossy(self, path: Path, *, encoding: str) -> str:
        raw = path.read_bytes()
        if b"\x00" in raw[:4096]:
            raise ValueError(f"archivo binario o no textual: {path}")
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            return raw.decode(encoding, errors="replace")

    def _is_hidden(self, path: Path, base_path: Path) -> bool:
        try:
            relative = path.relative_to(base_path)
        except ValueError:
            relative = path
        return any(part.startswith(".") for part in relative.parts if part not in {".", ".."})

    def _collect_project_markers(self, base_path: Path) -> list[str]:
        markers: list[str] = []
        for marker in PROJECT_MARKERS:
            if "*" in marker:
                for matched in base_path.glob(marker):
                    markers.append(matched.name)
            else:
                candidate = base_path / marker
                if candidate.exists():
                    markers.append(marker)
        seen: set[str] = set()
        ordered: list[str] = []
        for marker in markers:
            if marker not in seen:
                seen.add(marker)
                ordered.append(marker)
        return ordered

    def _detect_project_kinds(self, markers: list[str]) -> list[str]:
        kinds: list[str] = []
        marker_set = set(markers)
        if "package.json" in marker_set:
            kinds.append("node")
        if "pyproject.toml" in marker_set or "requirements.txt" in marker_set:
            kinds.append("python")
        if "Cargo.toml" in marker_set:
            kinds.append("rust")
        if "go.mod" in marker_set:
            kinds.append("go")
        if "composer.json" in marker_set:
            kinds.append("php")
        if "pom.xml" in marker_set:
            kinds.append("java")
        if any(marker.lower().endswith(".sln") for marker in marker_set):
            kinds.append("dotnet")
        if ".git" in marker_set:
            kinds.append("git")
        return kinds

    def _run_git(self, cwd: Path, args: list[str], *, timeout_seconds: int = 10) -> dict[str, Any]:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        return {
            "return_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    def _resolve_git_repo(self, path: str | None = None) -> dict[str, Any]:
        git_path = shutil.which("git")
        if not git_path:
            return {
                "available": False,
                "is_repo": False,
                "reason": "git no disponible en PATH",
            }

        root_info = self.find_project_root(path)
        candidate_root = Path(root_info["path"])
        top_level = self._run_git(candidate_root, ["rev-parse", "--show-toplevel"])
        if top_level["return_code"] != 0:
            return {
                "available": True,
                "is_repo": False,
                "path": str(candidate_root),
                "reason": top_level["stderr"] or "no es un repositorio git",
            }

        repo_root = Path((top_level["stdout"] or "").strip())
        return {
            "available": True,
            "is_repo": True,
            "repo_root": str(repo_root),
            "relative_repo_root": self._relative_path(repo_root),
        }

    def _resolve_relative_git_target(self, path: str | None, repo_root: Path) -> str | None:
        if not path:
            return None

        resolved = self.resolve_path(path)
        try:
            return str(resolved.relative_to(repo_root))
        except ValueError:
            return None

    def _detect_language(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".py":
            return "python"
        if suffix in {".js", ".jsx", ".mjs", ".cjs"}:
            return "javascript"
        if suffix in {".ts", ".tsx"}:
            return "typescript"
        if suffix in {".json"}:
            return "json"
        if suffix in {".md"}:
            return "markdown"
        return "text"

    def _python_outline(self, content: str, *, max_symbols: int) -> list[dict[str, Any]]:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return self._generic_outline(content, max_symbols=max_symbols)

        symbols: list[dict[str, Any]] = []

        def visit(node: ast.AST, parent: str | None = None) -> None:
            if len(symbols) >= max_symbols:
                return
            if isinstance(node, ast.ClassDef):
                symbols.append(
                    {
                        "name": node.name,
                        "kind": "class",
                        "line": getattr(node, "lineno", 0),
                        "end_line": getattr(node, "end_lineno", getattr(node, "lineno", 0)),
                        "container": parent or "",
                    }
                )
                next_parent = node.name
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(
                    {
                        "name": node.name,
                        "kind": "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function",
                        "line": getattr(node, "lineno", 0),
                        "end_line": getattr(node, "end_lineno", getattr(node, "lineno", 0)),
                        "container": parent or "",
                    }
                )
                next_parent = parent
            else:
                next_parent = parent

            for child in getattr(node, "body", []):
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, next_parent)

        for top_level in tree.body:
            if isinstance(top_level, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(top_level)
            if len(symbols) >= max_symbols:
                break
        return symbols

    def _js_ts_outline(self, content: str, *, max_symbols: int) -> list[dict[str, Any]]:
        patterns = [
            ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)),
            ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)),
            ("function", re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\(", re.MULTILINE)),
            ("function", re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?[^=]*=>", re.MULTILINE)),
            ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)),
            ("type", re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE)),
        ]
        symbols: list[dict[str, Any]] = []
        for kind, pattern in patterns:
            for match in pattern.finditer(content):
                if len(symbols) >= max_symbols:
                    return sorted(symbols, key=lambda item: item["line"])
                line = content[: match.start()].count("\n") + 1
                symbols.append(
                    {
                        "name": match.group(1),
                        "kind": kind,
                        "line": line,
                        "end_line": line,
                        "container": "",
                    }
                )
        symbols.sort(key=lambda item: item["line"])
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for symbol in symbols:
            key = (symbol["name"], symbol["line"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(symbol)
        return deduped[:max_symbols]

    def _generic_outline(self, content: str, *, max_symbols: int) -> list[dict[str, Any]]:
        symbols: list[dict[str, Any]] = []
        patterns = [
            ("heading", re.compile(r"^\s*#{1,6}\s+(.+?)\s*$", re.MULTILINE)),
            ("section", re.compile(r"^\s*(?:class|def|function)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)),
        ]
        for kind, pattern in patterns:
            for match in pattern.finditer(content):
                if len(symbols) >= max_symbols:
                    return symbols
                line = content[: match.start()].count("\n") + 1
                symbols.append(
                    {
                        "name": match.group(1).strip(),
                        "kind": kind,
                        "line": line,
                        "end_line": line,
                        "container": "",
                    }
                )
        return symbols

    def _find_sibling_related_files(self, path: Path) -> list[dict[str, Any]]:
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        candidates = [
            (parent / f"{stem}.test{suffix}", "archivo de test asociado"),
            (parent / f"{stem}.spec{suffix}", "archivo spec asociado"),
            (parent / f"{stem}.stories{suffix}", "archivo stories asociado"),
            (parent / f"{stem}.md", "documentacion asociada"),
        ]
        if stem.endswith(".test") or stem.endswith(".spec"):
            base_stem = stem.rsplit(".", 1)[0]
            candidates.append((parent / f"{base_stem}{suffix}", "archivo fuente asociado"))

        items: list[dict[str, Any]] = []
        for candidate, reason in candidates:
            if candidate.exists() and candidate.is_file() and candidate != path:
                items.append(
                    {
                        "path": str(candidate),
                        "relative_path": self._relative_path(candidate),
                        "reason": reason,
                    }
                )
        return items

    def _extract_import_paths(
        self,
        path: Path,
        content: str,
        language: str,
    ) -> list[tuple[Path, str]]:
        if language == "python":
            return self._python_import_paths(path, content)
        if language in {"javascript", "typescript"}:
            return self._js_ts_import_paths(path, content)
        return []

    def _python_import_paths(self, path: Path, content: str) -> list[tuple[Path, str]]:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        imports: list[tuple[Path, str]] = []
        project_root = self.find_project_root(str(path)).get("path")
        project_root_path = Path(str(project_root))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    resolved = self._resolve_python_module_path(project_root_path, alias.name)
                    if resolved is not None:
                        imports.append((resolved, f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                if node.level > 0:
                    base_dir = path.parent
                    for _ in range(max(0, node.level - 1)):
                        base_dir = base_dir.parent
                    if module_name:
                        relative_module = module_name.replace(".", os.sep)
                        candidates = [
                            base_dir / f"{relative_module}.py",
                            base_dir / relative_module / "__init__.py",
                        ]
                    else:
                        candidates = [base_dir / "__init__.py"]
                    for candidate in candidates:
                        if candidate.exists():
                            imports.append((candidate, f"from . import {module_name or '*'}"))
                elif module_name:
                    resolved = self._resolve_python_module_path(project_root_path, module_name)
                    if resolved is not None:
                        imports.append((resolved, f"from {module_name} import ..."))

        return imports

    def _resolve_python_module_path(self, project_root: Path, module_name: str) -> Path | None:
        if not module_name:
            return None
        module_path = module_name.replace(".", os.sep)
        search_roots = [project_root]
        if self._root_dir not in search_roots:
            search_roots.append(self._root_dir)

        for root in search_roots:
            candidates = [
                root / f"{module_path}.py",
                root / module_path / "__init__.py",
            ]
            for candidate in candidates:
                if candidate.exists():
                    return candidate
        return None

    def _js_ts_import_paths(self, path: Path, content: str) -> list[tuple[Path, str]]:
        imports: list[tuple[Path, str]] = []
        pattern = re.compile(
            r"""(?:import\s+.*?\s+from\s+|export\s+.*?\s+from\s+|require\()\s*['"]([^'"]+)['"]""",
            re.MULTILINE,
        )
        for match in pattern.finditer(content):
            target = match.group(1).strip()
            if not target.startswith("."):
                continue
            resolved = self._resolve_js_ts_path(path.parent, target)
            if resolved is not None:
                imports.append((resolved, f"import {target}"))
        return imports

    def _resolve_js_ts_path(self, base_dir: Path, target: str) -> Path | None:
        candidate_base = (base_dir / target).resolve()
        suffixes = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json"]
        for suffix in suffixes:
            candidate = Path(str(candidate_base) + suffix)
            if candidate.exists() and candidate.is_file():
                return candidate
        for index_name in ["index.ts", "index.tsx", "index.js", "index.jsx", "index.mjs", "index.cjs"]:
            candidate = candidate_base / index_name
            if candidate.exists() and candidate.is_file():
                return candidate
        return None
