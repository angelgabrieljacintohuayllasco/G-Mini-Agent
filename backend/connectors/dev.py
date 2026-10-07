"""
G-Mini Agent — Conectores para desarrollo.

- GitHub: repositorios, releases e issues (API pública; token opcional para
  repos privados o más cuota).
- Paquetes: última versión y datos de paquetes de PyPI y npm.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from backend.connectors.base import Connector, ConnectorAction, ConnectorError, ConnectorField

_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
_PKG_RE = re.compile(r"^(@[\w.-]+/)?[\w.-]+$")


def _clip(text: Any, limit: int = 600) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class GitHubConnector(Connector):
    id = "github"
    label = "GitHub"
    description = "Datos de repositorios, última release e issues de GitHub."
    category = "desarrollo"
    icon = "github"
    docs_url = "https://docs.github.com/rest"
    fields = [
        ConnectorField("token", "Token personal (opcional)", secret=True,
                       help="Solo lectura basta. Sin token: repos públicos y 60 consultas por hora."),
    ]

    def actions(self) -> list[ConnectorAction]:
        repo = {"type": "string", "required": True, "description": "owner/repo"}
        return [
            ConnectorAction("repo", "Resumen de un repositorio.", self.repo, {"repo": repo}),
            ConnectorAction("latest_release", "Última release publicada.", self.latest_release, {"repo": repo}),
            ConnectorAction(
                "issues", "Issues abiertos de un repo, opcionalmente filtrados por texto.", self.issues,
                {"repo": repo, "query": {"type": "string"},
                 "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 30}},
            ),
        ]

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = self.secret("token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _repo(value: str) -> str:
        value = str(value or "").strip().removeprefix("https://github.com/").strip("/")
        if not _REPO_RE.match(value):
            raise ConnectorError("Indica el repositorio como owner/repo.")
        return value

    async def repo(self, repo: str) -> dict[str, Any]:
        data = await self.get_json(f"https://api.github.com/repos/{self._repo(repo)}", headers=self._headers())
        return {
            "repo": data.get("full_name"),
            "descripcion": _clip(data.get("description")),
            "estrellas": data.get("stargazers_count"),
            "forks": data.get("forks_count"),
            "issues_abiertos": data.get("open_issues_count"),
            "lenguaje": data.get("language"),
            "licencia": (data.get("license") or {}).get("spdx_id"),
            "rama": data.get("default_branch"),
            "actualizado": data.get("pushed_at"),
            "url": data.get("html_url"),
        }

    async def latest_release(self, repo: str) -> dict[str, Any]:
        data = await self.get_json(
            f"https://api.github.com/repos/{self._repo(repo)}/releases/latest", headers=self._headers()
        )
        return {
            "version": data.get("tag_name"),
            "nombre": data.get("name"),
            "publicada": data.get("published_at"),
            "notas": _clip(data.get("body"), 2000),
            "archivos": [a.get("name") for a in data.get("assets") or []][:20],
            "url": data.get("html_url"),
        }

    async def issues(self, repo: str, query: str = "", limit: int = 10) -> dict[str, Any]:
        q = f"repo:{self._repo(repo)} is:issue is:open {query or ''}".strip()
        data = await self.get_json(
            "https://api.github.com/search/issues", headers=self._headers(),
            params={"q": q, "per_page": limit, "sort": "updated"},
        )
        return {
            "total": data.get("total_count"),
            "issues": [
                {"numero": it.get("number"), "titulo": it.get("title"), "autor": (it.get("user") or {}).get("login"),
                 "comentarios": it.get("comments"), "actualizado": it.get("updated_at"), "url": it.get("html_url")}
                for it in data.get("items") or []
            ],
        }


class PackagesConnector(Connector):
    id = "packages"
    label = "Paquetes (PyPI y npm)"
    description = "Última versión, licencia y requisitos de paquetes de Python (PyPI) y JavaScript (npm)."
    category = "desarrollo"
    icon = "package"

    def actions(self) -> list[ConnectorAction]:
        name = {"type": "string", "required": True}
        return [
            ConnectorAction("pypi", "Información de un paquete de PyPI.", self.pypi, {"name": name}),
            ConnectorAction("npm", "Información de un paquete de npm.", self.npm, {"name": name}),
        ]

    @staticmethod
    def _name(value: str) -> str:
        value = str(value or "").strip()
        if not _PKG_RE.match(value):
            raise ConnectorError("Nombre de paquete inválido.")
        return value

    async def pypi(self, name: str) -> dict[str, Any]:
        data = await self.get_json(f"https://pypi.org/pypi/{self._name(name)}/json")
        info = data.get("info") or {}
        return {
            "paquete": info.get("name"),
            "version": info.get("version"),
            "resumen": _clip(info.get("summary")),
            "python": info.get("requires_python"),
            "licencia": _clip(info.get("license_expression") or info.get("license"), 80),
            "web": info.get("home_page") or (info.get("project_urls") or {}).get("Homepage"),
            "dependencias": (info.get("requires_dist") or [])[:30],
        }

    async def npm(self, name: str) -> dict[str, Any]:
        pkg = self._name(name)
        data = await self.get_json(f"https://registry.npmjs.org/{quote(pkg, safe='@')}/latest")
        return {
            "paquete": data.get("name"),
            "version": data.get("version"),
            "descripcion": _clip(data.get("description")),
            "licencia": data.get("license"),
            "node": (data.get("engines") or {}).get("node"),
            "dependencias": sorted((data.get("dependencies") or {}).keys())[:30],
            "web": data.get("homepage"),
        }
