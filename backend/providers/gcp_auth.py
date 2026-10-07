"""
G-Mini Agent — Credenciales de Google Cloud para Vertex AI.

Resuelve proyecto, ubicación y credenciales para los clientes de Vertex
(LLM, TTS, imagen/video) en este orden:
  1. Archivo de cuenta de servicio configurado (credentials_file).
  2. Application Default Credentials (gcloud auth application-default login).
El proyecto se toma de la config; si está vacío se detecta desde las ADC o
desde `gcloud config`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
DEFAULT_LOCATION = "global"

_detected_project_cache: tuple[float, str] | None = None
_DETECT_TTL_SECONDS = 300.0


@dataclass
class VertexSettings:
    project: str
    location: str
    credentials_file: str = ""
    project_source: str = "config"      # config | service_account | adc | gcloud | none


def _gcloud_executable() -> str | None:
    for name in ("gcloud", "gcloud.cmd"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _project_from_service_account(path: str) -> str:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return str(data.get("project_id") or "").strip()
    except Exception as exc:
        logger.debug(f"gcp_auth: no se pudo leer {path}: {exc}")
        return ""


def _project_from_adc() -> str:
    try:
        import google.auth

        _creds, project = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
        return str(project or "").strip()
    except Exception as exc:
        logger.debug(f"gcp_auth: ADC no disponible: {exc}")
        return ""


def _project_from_gcloud() -> str:
    exe = _gcloud_executable()
    if not exe:
        return ""
    try:
        out = subprocess.run(
            [exe, "config", "get-value", "project"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        value = (out.stdout or "").strip().splitlines()
        return value[-1].strip() if value and "(unset)" not in value[-1] else ""
    except Exception as exc:
        logger.debug(f"gcp_auth: gcloud config fallo: {exc}")
        return ""


def detect_project(credentials_file: str = "") -> tuple[str, str]:
    """Detecta el proyecto GCP. Devuelve (project, source)."""
    global _detected_project_cache
    if credentials_file:
        project = _project_from_service_account(credentials_file)
        if project:
            return project, "service_account"

    now = time.monotonic()
    if _detected_project_cache and now - _detected_project_cache[0] < _DETECT_TTL_SECONDS:
        cached = _detected_project_cache[1]
        return (cached, "adc") if cached else ("", "none")

    project = _project_from_adc()
    source = "adc"
    if not project:
        project = _project_from_gcloud()
        source = "gcloud"
    _detected_project_cache = (now, project)
    return (project, source) if project else ("", "none")


def resolve_vertex_settings(section: dict[str, Any] | None) -> VertexSettings:
    """Normaliza la sección de config (providers.vertex o providers.google)."""
    section = section if isinstance(section, dict) else {}
    credentials_file = str(section.get("credentials_file") or "").strip()
    if credentials_file and not Path(credentials_file).is_file():
        logger.warning(f"gcp_auth: credentials_file no existe: {credentials_file}")
        credentials_file = ""
    location = str(section.get("location") or "").strip() or DEFAULT_LOCATION
    project = str(section.get("project_id") or "").strip()
    source = "config"
    if not project:
        project, source = detect_project(credentials_file)
    return VertexSettings(
        project=project,
        location=location,
        credentials_file=credentials_file,
        project_source=source,
    )


def load_credentials(credentials_file: str = "") -> Any:
    """Credenciales google-auth explícitas (None = que el SDK use las ADC)."""
    if not credentials_file:
        return None
    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_file(
        credentials_file, scopes=[CLOUD_PLATFORM_SCOPE]
    )


def make_genai_client(settings: VertexSettings) -> Any:
    """Cliente google-genai apuntando a Vertex AI."""
    from google import genai

    if not settings.project:
        raise ValueError(
            "Vertex AI necesita un proyecto de Google Cloud. Escríbelo en Ajustes > Proveedores > "
            "Vertex AI o ejecuta `gcloud auth application-default login` y `gcloud config set project <id>`."
        )
    credentials = load_credentials(settings.credentials_file)
    kwargs: dict[str, Any] = {
        "vertexai": True,
        "project": settings.project,
        "location": settings.location,
    }
    if credentials is not None:
        kwargs["credentials"] = credentials
    return genai.Client(**kwargs)


def auth_status(section: dict[str, Any] | None) -> dict[str, Any]:
    """Diagnóstico para la UI: qué credenciales hay y qué proyecto se usaría."""
    settings = resolve_vertex_settings(section)
    adc_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or str(
        Path(os.environ.get("APPDATA", Path.home() / ".config")) / "gcloud" / "application_default_credentials.json"
    )
    has_adc_file = Path(adc_path).is_file()
    return {
        "project": settings.project,
        "project_source": settings.project_source,
        "location": settings.location,
        "credentials_file": settings.credentials_file,
        "adc_file": adc_path if has_adc_file else "",
        "has_credentials": bool(settings.credentials_file) or has_adc_file,
        "gcloud_installed": _gcloud_executable() is not None,
        "ready": bool(settings.project) and (bool(settings.credentials_file) or has_adc_file),
    }
