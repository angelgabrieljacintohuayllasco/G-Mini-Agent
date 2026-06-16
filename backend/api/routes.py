"""
G-Mini Agent — REST API routes.
Endpoints para configuración, health, modelos y API keys.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import yaml

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from loguru import logger

from backend.config import config
from backend.core.modes import PREDEFINED_MODES, get_mode, get_mode_behavior_prompt, list_modes
from backend.core.mcp_registry import MCPRegistry
from backend.core.mcp_runtime import MCPRuntime
from backend.core.mcp_registry import get_mcp_registry
from backend.core.payment_registry import PaymentRegistry
from backend.core.prompt_manager import list_core_prompts, reset_prompt_override, set_prompt_override
from backend.core.cost_tracker import get_cost_tracker as get_cost_tracker_service
from backend.core.gateway_service import get_gateway
from backend.core.scheduler import get_scheduler
from backend.core.skill_registry import SkillRegistry
from backend.core.skill_runtime import SkillRuntime
from backend.core.workspace_manager import WorkspaceManager
from backend.voice.engine import (
    DEFAULT_TTS_ENGINE,
    DEFAULT_GOOGLE_VOICE,
    GOOGLE_VOICE_CATALOG,
    get_tts_engine_descriptor,
    list_tts_engines,
    migrate_voice_config,
    normalize_tts_engine,
)
from backend.api.schemas import (
    HealthResponse,
    ModelsResponse,
    ModelInfo,
    ConfigResponse,
    ConfigUpdateRequest,
    PaymentAccountsResponse,
    PaymentAccountInfo,
    PromptUpdateRequest,
    CustomModeUpsertRequest,
    APIKeySetRequest,
    SkillsCatalogResponse,
    SkillInfo,
    SkillInstallLocalRequest,
    SkillInstallGitRequest,
    SkillMutationResponse,
    SkillRunRequest,
    SkillRunResponse,
    MCPServersResponse,
    MCPServerInfo,
    MCPToolsResponse,
    MCPToolCallRequest,
    MCPToolCallResponse,
    SchedulerJobsResponse,
    SchedulerRunsResponse,
    SchedulerCheckpointsResponse,
    SchedulerRecoveryInfo,
    CostSummaryResponse,
    CostWeeklyReportResponse,
    CostEventsResponse,
    CostEventInfo,
    ScheduledJobInfo,
    ScheduledRunInfo,
    ScheduledCheckpointInfo,
    ScheduledJobCreateRequest,
    ScheduledJobUpdateRequest,
    ScheduledJobMutationResponse,
    SchedulerTriggerRequest,
    SchedulerTriggerResponse,
    GatewayStatusResponse,
    GatewaySessionsResponse,
    GatewayOutboxResponse,
    GatewayNotifyRequest,
    GatewayNotifyResponse,
    GatewayChannelInfo,
    GatewaySessionInfo,
    GatewayOutboxInfo,
    GatewayCredentialSetRequest,
    GatewayCredentialStatusInfo,
    GatewayCredentialStatusResponse,
    ActionExecuteRequest,
    ActionExecuteResponse,
    ActionListResponse,
    CostOptimizerStatusResponse,
    CostOptimizerPressureResponse,
)

router = APIRouter()

_start_time = time.time()

# ── Servir archivos multimedia generados ──────────────────────────
_GENERATED_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "generated"
_MIME_MAP = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
}


@router.get("/media/{filename}")
async def serve_generated_media(filename: str):
    import re
    if not re.match(r"^[\w\-]+\.\w{2,5}$", filename):
        raise HTTPException(400, "Nombre de archivo inválido")
    fpath = _GENERATED_DIR / filename
    if not fpath.exists() or not fpath.is_file():
        raise HTTPException(404, "Archivo no encontrado")
    if not fpath.resolve().is_relative_to(_GENERATED_DIR.resolve()):
        raise HTTPException(403, "Acceso denegado")
    suffix = fpath.suffix.lower()
    mime = _MIME_MAP.get(suffix, "application/octet-stream")
    return FileResponse(fpath, media_type=mime, filename=filename)


def _get_workspace_manager() -> WorkspaceManager:
    from backend.api.websocket_handler import _agent_core

    workspace = getattr(_agent_core, "_workspace", None) if _agent_core is not None else None
    if isinstance(workspace, WorkspaceManager):
        return workspace
    return WorkspaceManager()


def _get_skill_registry() -> SkillRegistry:
    return SkillRegistry()


def _get_mcp_registry() -> MCPRegistry:
    return get_mcp_registry()


def _get_payment_registry() -> PaymentRegistry:
    return PaymentRegistry()


def _get_mcp_runtime() -> MCPRuntime:
    registry = _get_mcp_registry()
    return MCPRuntime(registry)


def _get_skill_runtime() -> SkillRuntime:
    registry = _get_skill_registry()
    return SkillRuntime(registry)


def _get_scheduler_service():
    return get_scheduler()


def _get_gateway_service():
    return get_gateway()


def _get_cost_tracker():
    return get_cost_tracker_service()


def _get_current_agent_session() -> tuple[str | None, str]:
    from backend.api.websocket_handler import _agent_core

    if _agent_core is None:
        return None, ""
    memory = getattr(_agent_core, "memory", None)
    session_id = getattr(memory, "session_id", None) if memory is not None else None
    current_mode = str(getattr(_agent_core, "current_mode", "") or "")
    return session_id, current_mode


def _raise_workspace_http_error(exc: Exception) -> None:
    if isinstance(exc, (FileNotFoundError, IsADirectoryError, ValueError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Health ───────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        version=config.get("app", "version", default="0.1.0"),
        uptime_seconds=round(time.time() - _start_time, 1),
    )


# ── Models catalog (YAML source of truth) ────────────────────────

_MODELS_YAML_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "models.yaml"
_models_catalog_cache: dict | None = None


def _load_models_catalog() -> dict:
    """Lee y cachea data/models.yaml."""
    global _models_catalog_cache
    if _models_catalog_cache is not None:
        return _models_catalog_cache
    try:
        with open(_MODELS_YAML_PATH, "r", encoding="utf-8") as f:
            _models_catalog_cache = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.error(f"Error leyendo models.yaml: {exc}")
        _models_catalog_cache = {}
    return _models_catalog_cache


def _build_voice_metadata() -> dict[str, Any]:
    migrate_voice_config()

    requested_engine, normalization_warning = normalize_tts_engine(
        config.get("voice", "tts_primary", default=DEFAULT_TTS_ENGINE)
    )
    voice_id = str(config.get("voice", "elevenlabs_voice_id", default="") or "").strip()

    from backend.api.websocket_handler import _agent_core

    if _agent_core is not None and _agent_core.voice is not None:
        runtime = _agent_core.voice.get_tts_runtime_status()
    else:
        requested_meta = get_tts_engine_descriptor(requested_engine)
        runtime = {
            "requested_engine": requested_engine,
            "requested_label": requested_meta.get("label", requested_engine),
            "active_engine": "none",
            "active_label": get_tts_engine_descriptor("none").get("label", "Desactivado"),
            "available": False,
            "reason": "not_initialized",
            "message": "VoiceEngine no inicializado.",
            "warnings": [],
            "supports_numeric_speed": bool(
                requested_meta.get("supports_numeric_speed", False)
            ),
            "provider": requested_meta.get("provider", "unknown"),
        }

    if normalization_warning and normalization_warning not in runtime.get("warnings", []):
        runtime["warnings"] = [*runtime.get("warnings", []), normalization_warning]

    google_voice = str(config.get("voice", "google_voice", default=DEFAULT_GOOGLE_VOICE) or DEFAULT_GOOGLE_VOICE).strip()

    return {
        "engines": list_tts_engines(),
        "google_voices": GOOGLE_VOICE_CATALOG,
        "settings": {
            "tts_primary": requested_engine,
            "tts_speed": float(config.get("voice", "tts_speed", default=1.0)),
            "auto_tts": bool(config.get("voice", "auto_tts", default=False)),
            "enabled": bool(config.get("voice", "enabled", default=True)),
            "elevenlabs_voice_id": voice_id,
            "google_voice": google_voice,
        },
        "runtime": runtime,
    }


def _is_secret_voice_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    return (
        "api_key" in normalized
        or "token" in normalized
        or "secret" in normalized
        or normalized in {"google_api", "elevenlabs_api"}
    )


def _mask_debug_value(key: str, value: Any) -> Any:
    if not _is_secret_voice_key(key):
        return value
    raw = str(value or "")
    return {
        "redacted": True,
        "length": len(raw),
        "suffix": raw[-4:] if len(raw) > 4 else None,
    }


def _summarize_voice_runtime(runtime: dict[str, Any] | None) -> dict[str, Any]:
    runtime = runtime or {}
    return {
        "requested_engine": runtime.get("requested_engine"),
        "active_engine": runtime.get("active_engine"),
        "available": runtime.get("available"),
        "reason": runtime.get("reason"),
        "message": runtime.get("message"),
        "warnings": list(runtime.get("warnings") or []),
    }


@router.get("/models/catalog")
async def get_models_catalog():
    """Devuelve el catálogo completo de modelos desde data/models.yaml."""
    catalog = _load_models_catalog()
    return {
        "provider_labels": catalog.get("provider_labels", {}),
        "llm": catalog.get("llm", {}),
        "image": catalog.get("image", []),
        "video": catalog.get("video", []),
        "music": catalog.get("music", []),
    }


@router.post("/models/catalog/reload")
async def reload_models_catalog():
    """Fuerza recarga del catálogo de modelos (útil tras editar el YAML)."""
    global _models_catalog_cache
    _models_catalog_cache = None
    catalog = _load_models_catalog()
    return {"success": True, "providers": list(catalog.get("llm", {}).keys())}


# ── Models ───────────────────────────────────────────────────────

@router.get("/models", response_model=ModelsResponse)
async def list_models():
    """Lista todos los modelos disponibles de todos los proveedores.
    
    Usa data/models.yaml como fuente de IDs de modelos (fuente única de verdad)
    y config.default.yaml para los datos operacionales (api_key_vault, base_url).
    """
    models: list[ModelInfo] = []

    provider_configs = config.get("providers", default={})
    # Leer IDs de modelos desde el catálogo centralizado (data/models.yaml)
    catalog = _load_models_catalog()
    catalog_llm = catalog.get("llm", {})

    vision_models = {
        "gpt-5.4", "gpt-5.4-pro", "gpt-4.1",
        "claude-opus-4-6", "claude-sonnet-4-6",
        "gemini-3.1-pro-preview", "gemini-3-flash-preview",
        "grok-4-1-fast-reasoning", "grok-4-1-fast-non-reasoning", "grok-4",
    }

    for provider_name, pconf in provider_configs.items():
        if not isinstance(pconf, dict):
            continue

        is_local = provider_name in ("ollama", "lmstudio")

        # Verificar si tiene API key configurada (para cloud providers)
        if not is_local:
            vault = pconf.get("api_key_vault", "")
            api_key = config.get_api_key(vault) if vault else None
            # Si no tiene clave, aún la listamos pero marcamos

        # Preferir IDs del catálogo centralizado; fallback a config si no está en el catálogo
        model_ids = catalog_llm.get(provider_name) or pconf.get("models", [])

        for model_id in model_ids:
            models.append(ModelInfo(
                id=model_id,
                provider=provider_name,
                is_local=is_local,
                supports_vision=model_id in vision_models,
                supports_streaming=True,
            ))

    return ModelsResponse(
        models=models,
        current_provider=config.get("model_router", "default_provider", default="openai"),
        current_model=config.get("model_router", "default_model", default="gpt-5.4"),
    )


# ── Config ───────────────────────────────────────────────────────

@router.get("/config")
async def get_config():
    """Devuelve la configuración completa (sin API keys ni secciones sensibles)."""
    _HIDDEN_SECTIONS = {"security", "server"}
    data = {
        k: v for k, v in config.data.items()
        if k not in _HIDDEN_SECTIONS
    }
    # No exponer vault names directamente
    return ConfigResponse(success=True, data=data)


@router.get("/config/{section}")
async def get_config_section(section: str):
    """Devuelve una sección de la configuración."""
    data = config.get(section)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Sección '{section}' no encontrada")
    return ConfigResponse(success=True, data={section: data})


@router.get("/voice/metadata")
async def get_voice_metadata():
    """Devuelve motores TTS soportados y el estado runtime de voz."""
    return ConfigResponse(success=True, data=_build_voice_metadata())


@router.get("/skills/catalog", response_model=SkillsCatalogResponse)
async def get_skills_catalog():
    registry = _get_skill_registry()
    return SkillsCatalogResponse(**registry.list_catalog())


@router.get("/skills/catalog/{skill_id}", response_model=SkillInfo)
async def get_skill_detail(skill_id: str):
    registry = _get_skill_registry()
    try:
        return SkillInfo(**registry.get_skill(skill_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' no encontrada") from exc


@router.post("/skills/install/local", response_model=SkillMutationResponse)
async def install_skill_local(req: SkillInstallLocalRequest):
    registry = _get_skill_registry()
    try:
        skill = await asyncio.to_thread(
            registry.install_from_path,
            req.path,
            req.overwrite,
        )
        return SkillMutationResponse(success=True, action="install_local", skill=SkillInfo(**skill))
    except Exception as exc:
        return SkillMutationResponse(success=False, action="install_local", error=str(exc))


@router.post("/skills/install/git", response_model=SkillMutationResponse)
async def install_skill_git(req: SkillInstallGitRequest):
    registry = _get_skill_registry()
    try:
        skill = await asyncio.to_thread(
            registry.install_from_git,
            req.repo_url,
            req.ref.strip() or None,
            req.subdir.strip() or None,
            req.overwrite,
        )
        return SkillMutationResponse(success=True, action="install_git", skill=SkillInfo(**skill))
    except Exception as exc:
        return SkillMutationResponse(success=False, action="install_git", error=str(exc))


@router.put("/skills/{skill_id}/enable", response_model=SkillMutationResponse)
async def enable_skill(skill_id: str):
    registry = _get_skill_registry()
    try:
        skill = await asyncio.to_thread(registry.set_enabled, skill_id, True)
        return SkillMutationResponse(success=True, action="enable", skill=SkillInfo(**skill))
    except Exception as exc:
        return SkillMutationResponse(success=False, action="enable", error=str(exc))


@router.put("/skills/{skill_id}/disable", response_model=SkillMutationResponse)
async def disable_skill(skill_id: str):
    registry = _get_skill_registry()
    try:
        skill = await asyncio.to_thread(registry.set_enabled, skill_id, False)
        return SkillMutationResponse(success=True, action="disable", skill=SkillInfo(**skill))
    except Exception as exc:
        return SkillMutationResponse(success=False, action="disable", error=str(exc))


@router.delete("/skills/{skill_id}", response_model=SkillMutationResponse)
async def uninstall_skill(skill_id: str):
    registry = _get_skill_registry()
    try:
        data = await asyncio.to_thread(registry.uninstall, skill_id)
        return SkillMutationResponse(success=True, action="uninstall", data=data)
    except Exception as exc:
        return SkillMutationResponse(success=False, action="uninstall", error=str(exc))


@router.post("/skills/{skill_id}/run", response_model=SkillRunResponse)
async def run_skill(skill_id: str, req: SkillRunRequest):
    runtime = _get_skill_runtime()
    try:
        data = await asyncio.to_thread(
            runtime.run_tool,
            skill_id,
            req.tool,
            req.input,
            req.timeout_seconds,
        )
        return SkillRunResponse(**data)
    except Exception as exc:
        return SkillRunResponse(
            success=False,
            skill_id=skill_id,
            tool=req.tool,
            error=str(exc),
        )


@router.get("/mcp/servers", response_model=MCPServersResponse)
async def get_mcp_servers():
    registry = _get_mcp_registry()
    return MCPServersResponse(**registry.list_servers())


@router.get("/mcp/servers/{server_id}", response_model=MCPServerInfo)
async def get_mcp_server_detail(server_id: str):
    registry = _get_mcp_registry()
    try:
        return MCPServerInfo(**registry.get_server(server_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Servidor MCP '{server_id}' no encontrado") from exc


@router.get("/payments/accounts", response_model=PaymentAccountsResponse)
async def get_payment_accounts():
    registry = _get_payment_registry()
    return PaymentAccountsResponse(**registry.list_accounts())


@router.get("/payments/accounts/{account_id}", response_model=PaymentAccountInfo)
async def get_payment_account_detail(account_id: str):
    registry = _get_payment_registry()
    try:
        return PaymentAccountInfo(**registry.get_account(account_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Cuenta de pago '{account_id}' no encontrada") from exc


@router.get("/mcp/servers/{server_id}/tools", response_model=MCPToolsResponse)
async def list_mcp_tools(server_id: str, cursor: str | None = Query(default=None)):
    runtime = _get_mcp_runtime()
    try:
        data = await asyncio.to_thread(runtime.list_tools, server_id, cursor, None)
        return MCPToolsResponse(**data)
    except Exception as exc:
        return MCPToolsResponse(success=False, server_id=server_id, error=str(exc))


@router.post("/mcp/servers/{server_id}/tools/{tool_name}/call", response_model=MCPToolCallResponse)
async def call_mcp_tool(server_id: str, tool_name: str, req: MCPToolCallRequest):
    runtime = _get_mcp_runtime()
    try:
        data = await asyncio.to_thread(
            runtime.call_tool,
            server_id,
            tool_name,
            req.arguments,
            req.timeout_seconds,
        )
        return MCPToolCallResponse(**data)
    except Exception as exc:
        return MCPToolCallResponse(
            success=False,
            server_id=server_id,
            tool=tool_name,
            error=str(exc),
        )


@router.get("/scheduler/jobs", response_model=SchedulerJobsResponse)
async def list_scheduler_jobs():
    scheduler = _get_scheduler_service()
    await scheduler.initialize()
    jobs = await scheduler.list_jobs()
    return SchedulerJobsResponse(jobs=[ScheduledJobInfo(**item) for item in jobs])


@router.get("/scheduler/jobs/{job_id}", response_model=ScheduledJobInfo)
async def get_scheduler_job(job_id: str):
    scheduler = _get_scheduler_service()
    await scheduler.initialize()
    try:
        return ScheduledJobInfo(**(await scheduler.get_job(job_id)))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job programado '{job_id}' no encontrado") from exc


@router.post("/scheduler/jobs", response_model=ScheduledJobMutationResponse)
async def create_scheduler_job(req: ScheduledJobCreateRequest):
    scheduler = _get_scheduler_service()
    await scheduler.initialize()
    try:
        job = await scheduler.create_job(
            name=req.name,
            task_type=req.task_type,
            payload=req.payload,
            trigger_type=req.trigger_type,
            interval_seconds=req.interval_seconds,
            cron_expression=req.cron_expression,
            event_name=req.event_name,
            webhook_path=req.webhook_path,
            webhook_secret=req.webhook_secret,
            heartbeat_key=req.heartbeat_key,
            heartbeat_interval_seconds=req.heartbeat_interval_seconds,
            max_retries=req.max_retries,
            retry_backoff_seconds=req.retry_backoff_seconds,
            retry_backoff_multiplier=req.retry_backoff_multiplier,
            enabled=req.enabled,
        )
        return ScheduledJobMutationResponse(
            success=True,
            action="create",
            job=ScheduledJobInfo(**job),
        )
    except Exception as exc:
        return ScheduledJobMutationResponse(success=False, action="create", error=str(exc))


@router.put("/scheduler/jobs/{job_id}", response_model=ScheduledJobMutationResponse)
async def update_scheduler_job(job_id: str, req: ScheduledJobUpdateRequest):
    scheduler = _get_scheduler_service()
    await scheduler.initialize()
    try:
        job = await scheduler.update_job(
            job_id,
            name=req.name,
            payload=req.payload,
            interval_seconds=req.interval_seconds,
            cron_expression=req.cron_expression,
            event_name=req.event_name,
            webhook_path=req.webhook_path,
            webhook_secret=req.webhook_secret,
            heartbeat_key=req.heartbeat_key,
            heartbeat_interval_seconds=req.heartbeat_interval_seconds,
            max_retries=req.max_retries,
            retry_backoff_seconds=req.retry_backoff_seconds,
            retry_backoff_multiplier=req.retry_backoff_multiplier,
            enabled=req.enabled,
        )
        return ScheduledJobMutationResponse(
            success=True,
            action="update",
            job=ScheduledJobInfo(**job),
        )
    except Exception as exc:
        return ScheduledJobMutationResponse(success=False, action="update", error=str(exc))


@router.delete("/scheduler/jobs/{job_id}", response_model=ScheduledJobMutationResponse)
async def delete_scheduler_job(job_id: str):
    scheduler = _get_scheduler_service()
    await scheduler.initialize()
    try:
        data = await scheduler.delete_job(job_id)
        return ScheduledJobMutationResponse(
            success=True,
            action="delete",
            data=data,
        )
    except Exception as exc:
        return ScheduledJobMutationResponse(success=False, action="delete", error=str(exc))


@router.post("/scheduler/jobs/{job_id}/run", response_model=ScheduledJobMutationResponse)
async def run_scheduler_job(job_id: str):
    scheduler = _get_scheduler_service()
    await scheduler.initialize()
    try:
        run = await scheduler.run_job_now(job_id)
        return ScheduledJobMutationResponse(
            success=True,
            action="run",
            run=ScheduledRunInfo(**run),
        )
    except Exception as exc:
        return ScheduledJobMutationResponse(success=False, action="run", error=str(exc))


@router.get("/scheduler/runs", response_model=SchedulerRunsResponse)
async def list_scheduler_runs(
    job_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    scheduler = _get_scheduler_service()
    await scheduler.initialize()
    runs = await scheduler.list_runs(job_id=job_id, limit=limit)
    return SchedulerRunsResponse(runs=[ScheduledRunInfo(**item) for item in runs])


@router.get("/scheduler/checkpoints", response_model=SchedulerCheckpointsResponse)
async def list_scheduler_checkpoints(
    job_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    scheduler = _get_scheduler_service()
    await scheduler.initialize()
    checkpoints = await scheduler.list_checkpoints(
        job_id=job_id,
        run_id=run_id,
        limit=limit,
    )
    return SchedulerCheckpointsResponse(
        checkpoints=[ScheduledCheckpointInfo(**item) for item in checkpoints],
        recovery=SchedulerRecoveryInfo(**scheduler.get_recovery_summary()),
    )


@router.get("/scheduler/recovery", response_model=SchedulerRecoveryInfo)
async def get_scheduler_recovery():
    scheduler = _get_scheduler_service()
    await scheduler.initialize()
    return SchedulerRecoveryInfo(**scheduler.get_recovery_summary())


@router.get("/gateway/status", response_model=GatewayStatusResponse)
async def get_gateway_status():
    gateway = _get_gateway_service()
    await gateway.initialize()
    data = await gateway.get_status()
    channels = [GatewayChannelInfo(**item) for item in data.pop("channels", [])]
    return GatewayStatusResponse(channels=channels, **data)


@router.get("/gateway/sessions", response_model=GatewaySessionsResponse)
async def list_gateway_sessions(
    channel: str | None = Query(default=None),
    connected_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
):
    gateway = _get_gateway_service()
    await gateway.initialize()
    sessions = await gateway.list_sessions(
        channel=channel,
        connected_only=connected_only,
        limit=limit,
    )
    return GatewaySessionsResponse(sessions=[GatewaySessionInfo(**item) for item in sessions])


@router.get("/gateway/outbox", response_model=GatewayOutboxResponse)
async def list_gateway_outbox(
    channel: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    gateway = _get_gateway_service()
    await gateway.initialize()
    notifications = await gateway.list_outbox(
        channel=channel,
        status=status,
        limit=limit,
    )
    return GatewayOutboxResponse(
        notifications=[GatewayOutboxInfo(**item) for item in notifications]
    )


@router.get("/gateway/runtime/{channel}", response_model=ConfigResponse)
async def get_gateway_runtime_state(channel: str):
    gateway = _get_gateway_service()
    await gateway.initialize()
    normalized = str(channel or "").strip().lower()
    data = await gateway.get_state_value(f"{normalized}_runtime", {})
    return ConfigResponse(success=True, data=data if isinstance(data, dict) else {"value": data})


@router.post("/gateway/notify", response_model=GatewayNotifyResponse)
async def gateway_notify(req: GatewayNotifyRequest):
    gateway = _get_gateway_service()
    await gateway.initialize()
    try:
        data = await gateway.notify(
            title=req.title,
            body=req.body,
            target=req.target,
            level=req.level,
            payload=req.payload,
            source_type=req.source_type,
            source_id=req.source_id,
        )
        return GatewayNotifyResponse(
            success=True,
            notification=GatewayOutboxInfo(**data),
        )
    except Exception as exc:
        return GatewayNotifyResponse(success=False, error=str(exc))


@router.post("/gateway/credentials", response_model=GatewayCredentialStatusInfo)
async def set_gateway_credential(req: GatewayCredentialSetRequest):
    channel = str(req.channel or "").strip().lower()
    vault_defaults = {
        "telegram": "telegram_bot",
        "discord": "discord_bot",
    }
    if channel not in vault_defaults:
        raise HTTPException(status_code=400, detail=f"Canal gateway no soportado para credenciales: {channel}")

    vault_name = str(
        config.get("gateway", "channels", channel, "bot_token_vault", default=vault_defaults[channel])
        or vault_defaults[channel]
    ).strip() or vault_defaults[channel]
    config.set_api_key(vault_name, req.token)

    gateway = _get_gateway_service()
    await gateway.reload_config()
    masked = f"...{req.token[-4:]}" if len(req.token) > 4 else None
    return GatewayCredentialStatusInfo(channel=channel, configured=True, masked=masked)


@router.get("/gateway/credentials/status", response_model=GatewayCredentialStatusResponse)
async def get_gateway_credentials_status():
    channel_specs = {
        "telegram": str(
            config.get("gateway", "channels", "telegram", "bot_token_vault", default="telegram_bot")
            or "telegram_bot"
        ).strip() or "telegram_bot",
        "discord": str(
            config.get("gateway", "channels", "discord", "bot_token_vault", default="discord_bot")
            or "discord_bot"
        ).strip() or "discord_bot",
    }
    items: list[GatewayCredentialStatusInfo] = []
    for channel, vault_name in channel_specs.items():
        token = str(config.get_api_key(vault_name) or "").strip()
        items.append(
            GatewayCredentialStatusInfo(
                channel=channel,
                configured=bool(token),
                masked=f"...{token[-4:]}" if len(token) > 4 else None,
            )
        )
    return GatewayCredentialStatusResponse(credentials=items)


@router.get("/costs/summary", response_model=CostSummaryResponse)
async def get_costs_summary(
    session_id: str | None = Query(default=None),
    worker_id: str | None = Query(default=None),
    worker_kind: str | None = Query(default=None),
):
    tracker = _get_cost_tracker()
    await tracker.initialize()
    current_session_id, current_mode = _get_current_agent_session()
    resolved_session_id = session_id or current_session_id
    data = await tracker.get_summary(
        session_id=resolved_session_id,
        current_mode=current_mode,
        worker_id=worker_id,
        worker_kind=worker_kind or "",
    )
    return CostSummaryResponse(**data)


@router.get("/costs/reports/weekly", response_model=CostWeeklyReportResponse)
async def get_costs_weekly_report(
    session_id: str | None = Query(default=None),
    week_offset: int = Query(default=0, ge=0, le=12),
    include_current_week: bool = Query(default=False),
    top_n: int = Query(default=5, ge=1, le=20),
):
    tracker = _get_cost_tracker()
    await tracker.initialize()
    _, current_mode = _get_current_agent_session()
    data = await tracker.get_weekly_report(
        session_id=session_id,
        current_mode=current_mode,
        week_offset=week_offset,
        include_current_week=include_current_week,
        top_n=top_n,
    )
    return CostWeeklyReportResponse(**data)


@router.get("/costs/events", response_model=CostEventsResponse)
async def get_costs_events(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
):
    tracker = _get_cost_tracker()
    await tracker.initialize()
    current_session_id, _ = _get_current_agent_session()
    resolved_session_id = session_id or current_session_id
    events = await tracker.list_events(session_id=resolved_session_id, limit=limit)
    return CostEventsResponse(
        session_id=resolved_session_id,
        events=[CostEventInfo(**item) for item in events],
    )


# --- Cost Optimizer endpoints (Fase 9.4) ---

@router.get("/costs/optimizer/status", response_model=CostOptimizerStatusResponse)
async def get_cost_optimizer_status():
    from backend.core.cost_optimizer import get_cost_optimizer
    optimizer = get_cost_optimizer()
    data = optimizer.get_status()
    return CostOptimizerStatusResponse(**data)


@router.get("/costs/optimizer/pressure", response_model=CostOptimizerPressureResponse)
async def get_cost_optimizer_pressure(
    session_id: str | None = Query(default=None),
):
    from backend.core.cost_optimizer import get_cost_optimizer
    optimizer = get_cost_optimizer()
    current_session_id, current_mode = _get_current_agent_session()
    resolved_session_id = session_id or current_session_id
    data = await optimizer.get_budget_pressure(
        session_id=resolved_session_id or "",
        mode_key=current_mode,
    )
    return CostOptimizerPressureResponse(
        level=data.get("level", "none"),
        max_usage_percent=data.get("max_usage_percent", 0.0),
        scopes=data.get("scopes", []),
        stop_required=data.get("stop_required", False),
        alerts=data.get("alerts", []),
    )


@router.post("/costs/optimizer/invalidate")
async def invalidate_cost_optimizer_cache():
    from backend.core.cost_optimizer import get_cost_optimizer
    optimizer = get_cost_optimizer()
    optimizer.invalidate_cache()
    return {"success": True, "message": "Cache de presión invalidada"}


@router.post("/scheduler/events/{event_name}/emit", response_model=SchedulerTriggerResponse)
async def emit_scheduler_event(event_name: str, req: SchedulerTriggerRequest):
    scheduler = _get_scheduler_service()
    await scheduler.initialize()
    try:
        data = await scheduler.emit_event(event_name, payload=req.payload)
        return SchedulerTriggerResponse(success=True, **data)
    except Exception as exc:
        return SchedulerTriggerResponse(
            success=False,
            trigger_type="event",
            trigger_value=event_name,
            error=str(exc),
        )


@router.post("/scheduler/heartbeat/{heartbeat_key}/emit", response_model=SchedulerTriggerResponse)
async def emit_scheduler_heartbeat(heartbeat_key: str, req: SchedulerTriggerRequest):
    scheduler = _get_scheduler_service()
    await scheduler.initialize()
    try:
        data = await scheduler.emit_heartbeat(heartbeat_key, payload=req.payload)
        return SchedulerTriggerResponse(success=True, **data)
    except Exception as exc:
        return SchedulerTriggerResponse(
            success=False,
            trigger_type="heartbeat",
            trigger_value=heartbeat_key,
            error=str(exc),
        )


@router.post("/scheduler/webhooks/{webhook_path:path}", response_model=SchedulerTriggerResponse)
async def trigger_scheduler_webhook(
    webhook_path: str,
    req: SchedulerTriggerRequest,
    x_gmini_webhook_secret: str | None = Header(default=None, alias="X-GMini-Webhook-Secret"),
):
    scheduler = _get_scheduler_service()
    await scheduler.initialize()
    try:
        data = await scheduler.trigger_webhook(
            webhook_path,
            payload=req.payload,
            secret=x_gmini_webhook_secret or req.secret,
        )
        return SchedulerTriggerResponse(success=True, **data)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        return SchedulerTriggerResponse(
            success=False,
            trigger_type="webhook",
            trigger_value=webhook_path,
            error=str(exc),
        )


@router.put("/config")
async def update_config(req: ConfigUpdateRequest):
    try:
        from backend.api.websocket_handler import _agent_core

        target_key = req.key
        target_value = req.value
        response_data: dict[str, Any] = {}

        if req.section == "voice":
            if req.key == "tts_primary":
                target_value, _warning = normalize_tts_engine(req.value)
            elif req.key == "elevenlabs_default_voice":
                target_key = "elevenlabs_voice_id"
            elif req.key == "google_api_key":
                logger.warning(
                    "Voice config alias received via /config: req.key=voice.google_api_key. "
                    "Redirecting to keyring vault 'google_api'."
                )
                config.set_api_key("google_api", str(req.value or "").strip())
                persisted_voice_engine = config.get(
                    "voice", "tts_primary", default=DEFAULT_TTS_ENGINE
                )
                logger.info(
                    "Voice config alias applied: req.key=voice.google_api_key, "
                    f"configured={bool(str(req.value or '').strip())}, "
                    f"persisted_voice_tts_primary={persisted_voice_engine}"
                )
                if _agent_core is not None:
                    logger.info(
                        "Voice reload requested after voice.google_api_key alias save: "
                        f"persisted_voice_tts_primary={persisted_voice_engine}"
                    )
                    response_data["voice_runtime"] = await _agent_core.reload_voice_configuration(
                        reload_stt=False,
                        origin="config:voice.google_api_key",
                    )
                    logger.info(
                        "Voice reload completed after voice.google_api_key alias save: "
                        f"{_summarize_voice_runtime(response_data['voice_runtime'])}"
                    )
                return ConfigResponse(success=True, data=response_data or None)

            logger.info(
                "Voice config update requested: "
                f"req.key={req.key}, "
                f"req.value={_mask_debug_value(req.key, req.value)}, "
                f"target_key={target_key}, "
                f"target_value={_mask_debug_value(target_key, target_value)}"
            )

        config.set(req.section, target_key, value=target_value)

        if req.section == "voice":
            persisted_voice_engine = config.get("voice", "tts_primary", default=DEFAULT_TTS_ENGINE)
            logger.info(
                "Voice config persisted: "
                f"req.key={req.key}, "
                f"req.value={_mask_debug_value(req.key, req.value)}, "
                f"target_key={target_key}, "
                f"target_value={_mask_debug_value(target_key, target_value)}, "
                f"persisted_voice_tts_primary={persisted_voice_engine}"
            )

        if _agent_core is not None and req.section == "agent" and req.key in ("system_prompt_file", "autonomy_level"):
            _agent_core.reload_prompt_configuration()

        # Solo recargar el motor de voz cuando el cambio afecta al engine activo.
        # Claves que NO requieren reload: tts_speed, auto_tts, enabled, elevenlabs_voice_id, google_voice.
        _VOICE_KEYS_REQUIRING_RELOAD = {"tts_primary"}
        voice_needs_reload = (
            _agent_core is not None
            and req.section == "voice"
            and (target_key in _VOICE_KEYS_REQUIRING_RELOAD or str(target_key).startswith("stt_"))
        )
        if voice_needs_reload:
            reload_stt = str(target_key).startswith("stt_")
            logger.info(
                "Voice reload requested from /config: "
                f"origin=config:{req.section}.{target_key}, "
                f"reload_stt={reload_stt}, "
                f"persisted_voice_tts_primary={config.get('voice', 'tts_primary', default=DEFAULT_TTS_ENGINE)}"
            )
            response_data["voice_runtime"] = await _agent_core.reload_voice_configuration(
                reload_stt=reload_stt,
                origin=f"config:{req.section}.{target_key}",
            )
            logger.info(
                "Voice reload completed from /config: "
                f"origin=config:{req.section}.{target_key}, "
                f"runtime={_summarize_voice_runtime(response_data['voice_runtime'])}"
            )

            # Informar si el motor solicitado no pudo activarse
            if req.key == "tts_primary" and response_data.get("voice_runtime", {}).get("available") is False:
                reason = response_data["voice_runtime"].get("reason", "")
                message = response_data["voice_runtime"].get("message", "")
                if reason == "missing_key":
                    response_data["warning"] = (
                        f"No está configurada la API key necesaria para '{target_value}'. "
                        "Guarda la API key correspondiente para activar este motor."
                    )
                elif reason in ("init_error", "unsupported_model"):
                    response_data["warning"] = (
                        f"No se pudo inicializar el motor '{target_value}': {message}"
                    )

        if req.section == "mcp":
            registry = get_mcp_registry()
            registry.reload()
            if _agent_core is not None:
                _agent_core.reload_prompt_configuration()

        if req.section == "scheduler":
            scheduler = _get_scheduler_service()
            await scheduler.reload_config()

        if req.section == "gateway":
            gateway = _get_gateway_service()
            await gateway.reload_config()

        return ConfigResponse(success=True, data=response_data or None)

    except Exception as e:
        return ConfigResponse(success=False, error=str(e))

@router.get("/prompts")
async def get_prompts():
    mode_prompts = [
        {
            "key": f"mode_behavior.{mode.key}",
            "label": f"Modo: {mode.name}",
            "content": get_mode_behavior_prompt(mode.key),
            "source": "config" if config.get("prompts", "mode_behavior", mode.key, default="") else "default",
            "mode_key": mode.key,
        }
        for mode in PREDEFINED_MODES.values()
    ]
    return {
        "success": True,
        "data": {
            "core": list_core_prompts(),
            "modes": mode_prompts,
        },
    }


@router.put("/prompts")
async def update_prompt(req: PromptUpdateRequest):
    try:
        set_prompt_override(req.key, req.content)
        from backend.api.websocket_handler import _agent_core
        if _agent_core is not None:
            _agent_core.reload_prompt_configuration()
        return {"success": True, "key": req.key}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/prompts/{prompt_key:path}")
async def reset_prompt(prompt_key: str):
    try:
        reset_prompt_override(prompt_key)
        from backend.api.websocket_handler import _agent_core
        if _agent_core is not None:
            _agent_core.reload_prompt_configuration()
        return {"success": True, "key": prompt_key}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── API Keys ─────────────────────────────────────────────────────

@router.post("/api-keys")
async def set_api_key(req: APIKeySetRequest):
    """Guarda una API key en el OS keyring y reinicializa el provider."""
    vault_map = {
        "openai": "openai_api",
        "anthropic": "anthropic_api",
        "google": "google_api",
        "xai": "xai_api",
        "deepseek": "deepseek_api",
        "elevenlabs": "elevenlabs_api",
        "virustotal": "virustotal_api",
    }
    vault = vault_map.get(req.provider)
    if not vault:
        raise HTTPException(status_code=400, detail=f"Provider '{req.provider}' no reconocido")

    config.set_api_key(vault, req.api_key)
    logger.info(
        "API key saved: "
        f"provider={req.provider}, "
        f"vault={vault}, "
        f"configured={bool(str(req.api_key or '').strip())}, "
        f"voice_tts_primary_before_reload={config.get('voice', 'tts_primary', default=DEFAULT_TTS_ENGINE)}"
    )

    # Reinicializar el provider con la nueva key
    try:
        from backend.api.websocket_handler import _agent_core
        if _agent_core and _agent_core._router:
            provider_obj = _agent_core._router.get_provider(req.provider)
            if provider_obj and hasattr(provider_obj, '_configure'):
                provider_obj._configure()
        if _agent_core and req.provider in {"google", "elevenlabs"}:
            logger.info(
                "Voice reload requested after API key save: "
                f"provider={req.provider}, "
                f"voice_tts_primary_before_reload={config.get('voice', 'tts_primary', default=DEFAULT_TTS_ENGINE)}"
            )
            runtime = await _agent_core.reload_voice_configuration(
                reload_stt=False,
                origin=f"api-key:{req.provider}",
            )
            logger.info(
                "Voice reload completed after API key save: "
                f"provider={req.provider}, "
                f"runtime={_summarize_voice_runtime(runtime)}"
            )
    except Exception:
        pass  # Non-blocking: el provider se reconfigurará en el siguiente uso

    return {"success": True, "provider": req.provider}


@router.get("/api-keys/status")
async def api_keys_status():
    """Verifica qué API keys están configuradas (sin revelar valores)."""
    vaults = {
        "openai": "openai_api",
        "anthropic": "anthropic_api",
        "google": "google_api",
        "xai": "xai_api",
        "deepseek": "deepseek_api",
        "elevenlabs": "elevenlabs_api",
        "virustotal": "virustotal_api",
    }
    status = {}
    for provider, vault in vaults.items():
        key = config.get_api_key(vault)
        status[provider] = {
            "configured": key is not None and len(key) > 0,
            "masked": f"...{key[-4:]}" if key and len(key) > 4 else None,
        }
    return status


# ── Provider Selection ───────────────────────────────────────────

@router.put("/model")
async def set_active_model(data: dict):
    """Cambia el modelo y proveedor activos."""
    provider = data.get("provider")
    model = data.get("model")
    if provider:
        config.set("model_router", "default_provider", value=provider)
    if model:
        config.set("model_router", "default_model", value=model)
    return {
        "success": True,
        "provider": config.get("model_router", "default_provider"),
        "model": config.get("model_router", "default_model"),
    }

@router.get("/providers/google/backend")
async def get_google_backend_config():
    """Devuelve la configuración del backend de Google (AI Studio vs Vertex AI)."""
    return {
        "backend": config.get("providers", "google", "backend", default="ai_studio"),
        "project_id": config.get("providers", "google", "project_id", default=""),
        "location": config.get("providers", "google", "location", default="us-central1"),
        "credentials_file": config.get("providers", "google", "credentials_file", default=""),
    }


@router.put("/providers/google/backend")
async def set_google_backend_config(data: dict):
    """Cambia el backend de Google y reconfigura el provider."""
    from backend.api.websocket_handler import _agent_core

    backend = data.get("backend")
    project_id = data.get("project_id")
    location = data.get("location")
    credentials_file = data.get("credentials_file")

    if backend is not None:
        if backend not in ("ai_studio", "vertex_ai"):
            raise HTTPException(status_code=400, detail="backend debe ser 'ai_studio' o 'vertex_ai'")
        config.set("providers", "google", "backend", value=backend)
    if project_id is not None:
        config.set("providers", "google", "project_id", value=project_id)
    if location is not None:
        config.set("providers", "google", "location", value=location)
    if credentials_file is not None:
        config.set("providers", "google", "credentials_file", value=credentials_file)

    reconfigure_error = None
    try:
        if _agent_core and _agent_core._router:
            provider_obj = _agent_core._router.get_provider("google")
            if provider_obj and hasattr(provider_obj, '_configure'):
                provider_obj._configure()
    except Exception as exc:
        reconfigure_error = str(exc)
        logger.warning(f"Error reconfigurando Google provider: {exc}")

    return {
        "success": reconfigure_error is None,
        "backend": config.get("providers", "google", "backend", default="ai_studio"),
        "project_id": config.get("providers", "google", "project_id", default=""),
        "location": config.get("providers", "google", "location", default="us-central1"),
        "credentials_file": config.get("providers", "google", "credentials_file", default=""),
        "error": reconfigure_error,
    }


# ── Monitors ────────────────────────────────────────────────────

@router.get("/monitors")
async def list_monitors():
    """Lista los monitores disponibles y el monitor activo."""
    from backend.api.websocket_handler import _agent_core
    monitors = []
    if _agent_core and _agent_core._vision:
        monitors = _agent_core._vision.list_monitors()
    target = config.get("vision", "target_monitor", default=0)
    return {"monitors": monitors, "target_monitor": target}


@router.put("/monitors/target")
async def set_target_monitor(data: dict):
    """Cambia el monitor objetivo para screenshots y acciones."""
    monitor = data.get("monitor", 0)
    config.set("vision", "target_monitor", value=monitor)
    return {"success": True, "target_monitor": monitor}


# ── Modes ───────────────────────────────────────────────────────

@router.get("/modes")
async def get_modes():
    """Lista los modos disponibles y el modo activo."""
    from backend.api.websocket_handler import _agent_core
    if _agent_core is None:
        raise HTTPException(status_code=500, detail="AgentCore no inicializado")
    return _agent_core.get_modes()


@router.put("/modes/{mode_key}")
async def set_mode(mode_key: str):
    """Cambia el modo activo del agente."""
    from backend.api.websocket_handler import _agent_core
    if _agent_core is None:
        raise HTTPException(status_code=500, detail="AgentCore no inicializado")
    return _agent_core.set_mode(mode_key)


@router.put("/modes/custom/{mode_key}")
async def upsert_custom_mode(mode_key: str, req: CustomModeUpsertRequest):
    mode_key = mode_key.strip().lower()
    if not mode_key:
        raise HTTPException(status_code=400, detail="mode_key inválido")
    if mode_key in PREDEFINED_MODES:
        raise HTTPException(status_code=400, detail="No puedes sobrescribir un modo predefinido")

    config.set(
        "modes",
        "custom",
        mode_key,
        value={
            "name": req.name,
            "description": req.description,
            "icon": req.icon,
            "behavior_prompt": req.behavior_prompt,
            "system_prompt": req.system_prompt,
            "allowed_capabilities": req.allowed_capabilities,
            "restricted_capabilities": req.restricted_capabilities,
            "requires_scope_confirmation": req.requires_scope_confirmation,
        },
    )

    from backend.api.websocket_handler import _agent_core
    if _agent_core is not None:
        active_mode = get_mode(_agent_core.current_mode)
        if active_mode.key == mode_key:
            _agent_core.set_mode(mode_key)

    return {
        "success": True,
        "mode": next((item for item in list_modes() if item["key"] == mode_key), None),
    }


@router.delete("/modes/custom/{mode_key}")
async def delete_custom_mode(mode_key: str):
    mode_key = mode_key.strip().lower()
    if not mode_key:
        raise HTTPException(status_code=400, detail="mode_key inválido")
    if mode_key in PREDEFINED_MODES:
        raise HTTPException(status_code=400, detail="No puedes borrar un modo predefinido")

    config.unset("modes", "custom", mode_key)

    from backend.api.websocket_handler import _agent_core
    if _agent_core is not None and _agent_core.current_mode == mode_key:
        _agent_core.set_mode("normal")

    return {"success": True, "deleted": mode_key}


@router.get("/subagents")
async def get_subagents():
    """Lista sub-agentes conocidos por el orquestador."""
    from backend.api.websocket_handler import _agent_core
    if _agent_core is None:
        raise HTTPException(status_code=500, detail="AgentCore no inicializado")
    return _agent_core.list_subagents()


@router.get("/terminals")
async def get_terminals():
    """Lista terminales detectadas y sesiones recientes."""
    from backend.api.websocket_handler import _agent_core
    if _agent_core is None:
        raise HTTPException(status_code=500, detail="AgentCore no inicializado")
    return _agent_core.list_terminals()


@router.get("/workspace/snapshot")
async def get_workspace_snapshot(
    path: str | None = None,
    max_entries: int = Query(default=80, ge=1, le=500),
    include_git: bool = True,
):
    workspace = _get_workspace_manager()
    try:
        return workspace.workspace_snapshot(
            path=path,
            max_entries=max_entries,
            include_git=include_git,
        )
    except Exception as exc:
        _raise_workspace_http_error(exc)


@router.get("/workspace/list")
async def list_workspace_files(
    path: str | None = None,
    pattern: str = "*",
    recursive: bool = False,
    include_hidden: bool = False,
    include_dirs: bool = True,
    max_results: int = Query(default=200, ge=1, le=1000),
):
    workspace = _get_workspace_manager()
    try:
        return workspace.list_files(
            path=path,
            pattern=pattern,
            recursive=recursive,
            include_hidden=include_hidden,
            include_dirs=include_dirs,
            max_results=max_results,
        )
    except Exception as exc:
        _raise_workspace_http_error(exc)


@router.get("/workspace/file")
async def read_workspace_file(
    path: str,
    start_line: int = Query(default=1, ge=1, le=1_000_000),
    max_lines: int = Query(default=300, ge=1, le=5000),
):
    workspace = _get_workspace_manager()
    try:
        return workspace.read_text_file(
            path=path,
            start_line=start_line,
            max_lines=max_lines,
        )
    except Exception as exc:
        _raise_workspace_http_error(exc)


@router.get("/workspace/search")
async def search_workspace_text(
    query: str,
    path: str | None = None,
    pattern: str = "*",
    recursive: bool = True,
    case_sensitive: bool = False,
    max_results: int = Query(default=100, ge=1, le=1000),
):
    workspace = _get_workspace_manager()
    try:
        return workspace.search_text(
            query=query,
            path=path,
            pattern=pattern,
            recursive=recursive,
            case_sensitive=case_sensitive,
            max_results=max_results,
        )
    except Exception as exc:
        _raise_workspace_http_error(exc)


@router.get("/workspace/git/status")
async def get_workspace_git_status(
    path: str | None = None,
    max_entries: int = Query(default=100, ge=1, le=1000),
):
    workspace = _get_workspace_manager()
    try:
        return workspace.git_status(path=path, max_entries=max_entries)
    except Exception as exc:
        _raise_workspace_http_error(exc)


@router.get("/workspace/git/changed")
async def get_workspace_git_changed_files(
    path: str | None = None,
    staged: bool = False,
    max_entries: int = Query(default=100, ge=1, le=1000),
):
    workspace = _get_workspace_manager()
    try:
        return workspace.git_changed_files(
            path=path,
            staged=staged,
            max_entries=max_entries,
        )
    except Exception as exc:
        _raise_workspace_http_error(exc)


@router.get("/workspace/git/diff")
async def get_workspace_git_diff(
    path: str | None = None,
    staged: bool = False,
    ref: str | None = None,
    max_chars: int = Query(default=20_000, ge=100, le=200_000),
):
    workspace = _get_workspace_manager()
    try:
        return workspace.git_diff(
            path=path,
            staged=staged,
            ref=ref,
            max_chars=max_chars,
        )
    except Exception as exc:
        _raise_workspace_http_error(exc)


@router.get("/workspace/code/outline")
async def get_workspace_code_outline(
    path: str,
    max_symbols: int = Query(default=200, ge=1, le=1000),
):
    workspace = _get_workspace_manager()
    try:
        return workspace.code_outline(path=path, max_symbols=max_symbols)
    except Exception as exc:
        _raise_workspace_http_error(exc)


@router.get("/workspace/code/related")
async def get_workspace_related_files(
    path: str,
    max_results: int = Query(default=20, ge=1, le=200),
):
    workspace = _get_workspace_manager()
    try:
        return workspace.code_related_files(path=path, max_results=max_results)
    except Exception as exc:
        _raise_workspace_http_error(exc)


# ── Sessions (Chat History) ──────────────────────────────────────

@router.get("/sessions")
async def list_sessions(limit: int = 30):
    """Lista las sesiones de chat guardadas."""
    from backend.api.websocket_handler import _agent_core
    if _agent_core is None or _agent_core._memory is None:
        return {"sessions": [], "current_session": None}
    
    sessions = await _agent_core._memory.list_sessions(limit=limit)
    return {
        "sessions": sessions,
        "current_session": _agent_core._memory.session_id,
    }


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Obtiene los mensajes de una sesión específica."""
    from backend.api.websocket_handler import _agent_core
    if _agent_core is None or _agent_core._memory is None:
        raise HTTPException(status_code=500, detail="AgentCore no inicializado")
    
    # Crear una memoria temporal para cargar la sesión
    from backend.core.memory import Memory
    temp_memory = Memory()
    await temp_memory.initialize()
    await temp_memory.load_session(session_id)
    
    messages = [
        {"role": m["role"], "content": m["content"], "timestamp": m.get("timestamp", ""),
         "message_type": m.get("message_type", "text"), "metadata": m.get("metadata", {})}
        for m in temp_memory.all_messages
    ]
    
    return {
        "session_id": session_id,
        "messages": messages,
        "message_count": len(messages),
        "mode": temp_memory.session_mode,
    }


@router.post("/sessions/new")
async def create_new_session():
    """Crea una nueva sesión de chat."""
    from backend.api.websocket_handler import _agent_core
    if _agent_core is None or _agent_core._memory is None:
        raise HTTPException(status_code=500, detail="AgentCore no inicializado")
    
    session_id = await _agent_core.new_session()
    return {
        "success": True,
        "session_id": session_id,
        "mode": _agent_core.current_mode,
    }


@router.post("/sessions/{session_id}/load")
async def load_session(session_id: str):
    """Carga una sesión existente como la sesión actual."""
    from backend.api.websocket_handler import _agent_core
    if _agent_core is None or _agent_core._memory is None:
        raise HTTPException(status_code=500, detail="AgentCore no inicializado")
    
    await _agent_core.load_session(session_id)
    
    messages = [
        {"role": m["role"], "content": m["content"], "timestamp": m.get("timestamp", ""),
         "message_type": m.get("message_type", "text"), "metadata": m.get("metadata", {})}
        for m in _agent_core._memory.all_messages
    ]
    
    return {
        "success": True,
        "session_id": session_id,
        "messages": messages,
        "mode": _agent_core.current_mode,
    }


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Elimina una sesión de chat."""
    from backend.core.memory import DB_PATH
    import aiosqlite
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        await db.commit()
    
    return {"success": True, "deleted": session_id}


@router.put("/sessions/{session_id}/title")
async def update_session_title(session_id: str, data: dict):
    """Actualiza el título de una sesión."""
    from backend.core.memory import DB_PATH
    import aiosqlite
    
    title = data.get("title", "")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET title = ? WHERE session_id = ?",
            (title, session_id),
        )
        await db.commit()
    
    return {"success": True, "session_id": session_id, "title": title}


# ── Actions Dispatcher ──────────────────────────────────────────


@router.post("/actions/execute", response_model=ActionExecuteResponse)
async def execute_action(req: ActionExecuteRequest):
    """
    Ejecuta una acción directa sin pasar por el LLM.
    Valida contra el PolicyEngine (modo, capability, autonomía).
    """
    from backend.api.websocket_handler import _agent_core

    if _agent_core is None:
        raise HTTPException(status_code=503, detail="AgentCore no inicializado")

    planner = getattr(_agent_core, "_planner", None)
    if planner is None:
        raise HTTPException(
            status_code=503,
            detail="ActionPlanner no disponible. Los módulos de Phase 2 (visión/automatización) pueden no estar cargados.",
        )

    from backend.core.planner import Action
    from backend.core.policy import PolicyEngine

    action = Action(type=req.action_type, params=req.params, description=req.description)
    policy = PolicyEngine()
    mode_key = _agent_core.current_mode

    review = policy.review_actions([action], mode_key=mode_key)
    if review.get("blocked"):
        reasons = [
            f"{item.get('action', req.action_type)}: {item.get('reason', 'bloqueado')}"
            for item in review.get("findings", [])
        ]
        return ActionExecuteResponse(
            success=False,
            action_type=req.action_type,
            policy_blocked=True,
            policy_reason="; ".join(reasons) or "Acción bloqueada por la política del modo activo",
        )

    if review.get("requires_approval"):
        return ActionExecuteResponse(
            success=False,
            action_type=req.action_type,
            policy_blocked=True,
            policy_reason="Esta acción requiere aprobación humana en el nivel de autonomía actual. Usa el chat para ejecutarla.",
        )

    try:
        result = await planner.execute_actions([action])
        first = result[0] if result else {}
        return ActionExecuteResponse(
            success=first.get("success", False),
            action_type=req.action_type,
            result=first,
        )
    except Exception as exc:
        return ActionExecuteResponse(
            success=False,
            action_type=req.action_type,
            error=str(exc),
        )


@router.get("/actions/available", response_model=ActionListResponse)
async def list_available_actions():
    """
    Lista las acciones disponibles filtradas por el modo activo.
    Solo incluye acciones cuya capability está permitida en el modo actual.
    """
    from backend.api.websocket_handler import _agent_core
    from backend.core.modes import get_mode, DEFAULT_MODE_KEY
    from backend.core.policy import PolicyEngine

    mode_key = _agent_core.current_mode if _agent_core else DEFAULT_MODE_KEY
    mode = get_mode(mode_key)
    policy = PolicyEngine()

    from backend.core.planner import Action

    # Mapa representativo de action_type → capability
    representative_actions = [
        ("screenshot", {}),
        ("click", {"x": 0, "y": 0}),
        ("double_click", {"x": 0, "y": 0}),
        ("right_click", {"x": 0, "y": 0}),
        ("type", {"text": ""}),
        ("focus_type", {"text": ""}),
        ("press", {"key": "enter"}),
        ("hotkey", {"keys": "ctrl+c"}),
        ("scroll", {"direction": "down"}),
        ("move", {"x": 0, "y": 0}),
        ("drag", {"start_x": 0, "start_y": 0, "end_x": 0, "end_y": 0}),
        ("browser_navigate", {"url": ""}),
        ("browser_click", {"selector": ""}),
        ("browser_type", {"selector": "", "text": ""}),
        ("browser_press", {"key": "enter"}),
        ("browser_snapshot", {}),
        ("browser_extract", {"selector": ""}),
        ("browser_eval", {"script": "document.title"}),
        ("browser_download_click", {"selector": ""}),
        ("adb_tap", {"x": 0, "y": 0}),
        ("adb_swipe", {"start_x": 0, "start_y": 0, "end_x": 0, "end_y": 0}),
        ("adb_text", {"text": ""}),
        ("terminal_run", {"command": ""}),
        ("file_read_text", {"path": ""}),
        ("file_write_text", {"path": "", "text": ""}),
        ("file_list", {"path": ""}),
        ("file_exists", {"path": ""}),
        ("file_search_text", {"query": ""}),
        ("file_replace_text", {"path": "", "find": "", "replace": ""}),
        ("task_complete", {"summary": ""}),
        ("wait", {"seconds": 1}),
        ("ide_open_file", {"path": ""}),
        ("ide_apply_edit", {"path": "", "edit": ""}),
        ("git_status", {}),
        ("git_diff", {}),
        ("skill_run", {"skill_id": "", "tool": ""}),
        ("mcp_call_tool", {"server_id": "", "tool": "", "arguments": {}}),
        ("gateway_notify", {"message": ""}),
    ]

    actions_list: list[dict[str, Any]] = []
    for action_type, params in representative_actions:
        action = Action(type=action_type, params=params)
        review = policy.review_actions([action], mode_key=mode_key)
        blocked = review.get("blocked", False)
        needs_approval = review.get("requires_approval", False)
        finding = review["findings"][0] if review.get("findings") else {}

        actions_list.append({
            "action_type": action_type,
            "capability": finding.get("capability", ""),
            "category": finding.get("category", ""),
            "severity": finding.get("severity", ""),
            "allowed": not blocked,
            "requires_approval": needs_approval,
            "reason": finding.get("reason", ""),
        })

    return ActionListResponse(
        mode=mode.key,
        mode_name=mode.name,
        actions=actions_list,
        total=len(actions_list),
    )


# ── Nodes (Phase 7) ──────────────────────────────────────────────

@router.get("/nodes")
async def list_nodes(include_disconnected: bool = True):
    from backend.core.node_manager import get_node_manager
    mgr = get_node_manager()
    nodes = await mgr.list_nodes(include_disconnected=include_disconnected)
    return {"ok": True, "nodes": nodes, "total": len(nodes), "connected": mgr.connected_count()}


@router.post("/nodes/pair")
async def create_pairing_token(request: Request):
    from backend.core.node_manager import get_node_manager
    body = await request.json()
    mgr = get_node_manager()
    result = await mgr.create_pairing_token(
        name=body.get("name", "Nuevo nodo"),
        node_type=body.get("node_type", "custom"),
        surfaces=body.get("surfaces"),
        ttl_seconds=body.get("ttl_seconds", 300),
    )
    return {"ok": True, **result}


@router.get("/nodes/{node_id}")
async def get_node(node_id: str):
    from backend.core.node_manager import get_node_manager
    mgr = get_node_manager()
    node = await mgr.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Nodo no encontrado")
    info = node.to_dict()
    info.pop("pairing_token", None)
    info.pop("ws_sid", None)
    return {"ok": True, "node": info}


@router.delete("/nodes/{node_id}")
async def remove_node(node_id: str):
    from backend.core.node_manager import get_node_manager
    mgr = get_node_manager()
    removed = await mgr.remove_node(node_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Nodo no encontrado")
    return {"ok": True}


@router.put("/nodes/{node_id}/permissions")
async def update_node_permissions(node_id: str, request: Request):
    from backend.core.node_manager import get_node_manager
    body = await request.json()
    mgr = get_node_manager()
    ok = await mgr.update_permissions(node_id, body.get("permissions", {}))
    if not ok:
        raise HTTPException(status_code=404, detail="Nodo no encontrado")
    return {"ok": True}


@router.post("/nodes/{node_id}/invoke")
async def invoke_node_surface(node_id: str, request: Request):
    from backend.core.node_manager import get_node_manager
    body = await request.json()
    mgr = get_node_manager()
    result = await mgr.invoke_surface(
        node_id=node_id,
        surface=body.get("surface", ""),
        params=body.get("params"),
        timeout=body.get("timeout", 30.0),
    )
    return result


# ── Smart Home (Phase 7) ─────────────────────────────────────────

@router.get("/smart-home/devices")
async def list_smart_home_devices(device_type: str | None = None, area: str | None = None):
    from backend.core.smart_home_manager import get_smart_home
    mgr = get_smart_home()
    devices = await mgr.list_devices(device_type=device_type, area=area)
    return {"ok": True, "devices": devices, "total": len(devices)}


@router.get("/smart-home/summary")
async def smart_home_summary():
    from backend.core.smart_home_manager import get_smart_home
    mgr = get_smart_home()
    summary = await mgr.get_home_summary()
    return {"ok": True, **summary}


@router.post("/smart-home/refresh")
async def refresh_smart_home():
    from backend.core.smart_home_manager import get_smart_home
    mgr = get_smart_home()
    count = await mgr.refresh_devices()
    return {"ok": True, "devices_found": count}


@router.get("/smart-home/devices/{device_id}/state")
async def get_device_state(device_id: str):
    from backend.core.smart_home_manager import get_smart_home
    mgr = get_smart_home()
    state = await mgr.get_device_state(device_id)
    return {"ok": True, "state": state}


@router.post("/smart-home/devices/{device_id}/control")
async def control_device(device_id: str, request: Request):
    from backend.core.smart_home_manager import get_smart_home
    body = await request.json()
    mgr = get_smart_home()
    action = body.pop("action", "")
    result = await mgr.control_device(device_id, action, **body)
    return result


@router.post("/smart-home/devices/{device_id}/state")
async def set_device_state(device_id: str, request: Request):
    from backend.core.smart_home_manager import get_smart_home
    body = await request.json()
    mgr = get_smart_home()
    state = body.pop("state", "")
    result = await mgr.set_device_state(device_id, state, **body)
    return result


@router.get("/smart-home/automations")
async def list_automations():
    from backend.core.smart_home_manager import get_smart_home
    mgr = get_smart_home()
    autos = await mgr.list_automations()
    return {"ok": True, "automations": autos, "total": len(autos)}


@router.post("/smart-home/automations")
async def create_automation(request: Request):
    from backend.core.smart_home_manager import get_smart_home
    body = await request.json()
    mgr = get_smart_home()
    auto = await mgr.create_automation(
        name=body.get("name", ""),
        trigger_type=body.get("trigger_type", "manual"),
        trigger_config=body.get("trigger_config", {}),
        actions=body.get("actions", []),
    )
    return {"ok": True, "automation": auto.to_dict()}


@router.delete("/smart-home/automations/{automation_id}")
async def delete_automation(automation_id: str):
    from backend.core.smart_home_manager import get_smart_home
    mgr = get_smart_home()
    ok = await mgr.delete_automation(automation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Automatización no encontrada")
    return {"ok": True}


@router.post("/smart-home/automations/{automation_id}/run")
async def run_automation(automation_id: str):
    from backend.core.smart_home_manager import get_smart_home
    mgr = get_smart_home()
    result = await mgr.execute_automation(automation_id)
    return result


# ── Canvas (Phase 8) ─────────────────────────────────────────────

@router.get("/canvas")
async def list_canvases(canvas_type: str | None = None, pinned_only: bool = False):
    from backend.core.canvas import get_canvas_service
    svc = get_canvas_service()
    canvases = await svc.list_canvases(canvas_type=canvas_type, pinned_only=pinned_only)
    return {"ok": True, "canvases": canvases, "total": len(canvases)}


@router.post("/canvas")
async def create_canvas(request: Request):
    from backend.core.canvas import get_canvas_service
    body = await request.json()
    svc = get_canvas_service()
    canvas = await svc.create_canvas(
        title=body.get("title", "Sin título"),
        canvas_type=body.get("canvas_type", "custom"),
        data=body.get("data"),
        content=body.get("content"),
        created_by=body.get("created_by", "agent"),
    )
    return {"ok": True, "canvas": canvas.to_dict()}


@router.get("/canvas/{canvas_id}")
async def get_canvas(canvas_id: str):
    from backend.core.canvas import get_canvas_service
    svc = get_canvas_service()
    canvas = await svc.get_canvas(canvas_id)
    if not canvas:
        raise HTTPException(status_code=404, detail="Canvas no encontrado")
    return {"ok": True, "canvas": canvas.to_dict()}


@router.put("/canvas/{canvas_id}")
async def update_canvas(canvas_id: str, request: Request):
    from backend.core.canvas import get_canvas_service
    body = await request.json()
    svc = get_canvas_service()
    canvas = await svc.update_canvas(
        canvas_id,
        data=body.get("data"),
        content=body.get("content"),
        title=body.get("title"),
    )
    if not canvas:
        raise HTTPException(status_code=404, detail="Canvas no encontrado")
    return {"ok": True, "canvas": canvas.to_dict()}


@router.delete("/canvas/{canvas_id}")
async def delete_canvas(canvas_id: str):
    from backend.core.canvas import get_canvas_service
    svc = get_canvas_service()
    ok = await svc.delete_canvas(canvas_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Canvas no encontrado")
    return {"ok": True}


@router.put("/canvas/{canvas_id}/pin")
async def pin_canvas(canvas_id: str, request: Request):
    from backend.core.canvas import get_canvas_service
    body = await request.json()
    svc = get_canvas_service()
    ok = await svc.pin_canvas(canvas_id, body.get("pinned", True))
    if not ok:
        raise HTTPException(status_code=404, detail="Canvas no encontrado")
    return {"ok": True}


@router.get("/canvas/{canvas_id}/versions")
async def get_canvas_versions(canvas_id: str, limit: int = 20):
    from backend.core.canvas import get_canvas_service
    svc = get_canvas_service()
    versions = await svc.get_versions(canvas_id, limit=limit)
    return {"ok": True, "versions": versions}


@router.get("/canvas/{canvas_id}/versions/{version}")
async def get_canvas_version_content(canvas_id: str, version: int):
    from backend.core.canvas import get_canvas_service
    svc = get_canvas_service()
    ver = await svc.get_version_content(canvas_id, version)
    if not ver:
        raise HTTPException(status_code=404, detail="Versión no encontrada")
    return {"ok": True, "version": ver}


@router.post("/canvas/{canvas_id}/restore/{version}")
async def restore_canvas_version(canvas_id: str, version: int):
    from backend.core.canvas import get_canvas_service
    svc = get_canvas_service()
    canvas = await svc.restore_version(canvas_id, version)
    if not canvas:
        raise HTTPException(status_code=404, detail="Canvas o versión no encontrada")
    return {"ok": True, "canvas": canvas.to_dict()}


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 10 — Security / RBAC / Audit
# ═══════════════════════════════════════════════════════════════════════════

# ── RBAC ─────────────────────────────────────────────────────────────────

@router.get("/security/rbac/users")
async def list_rbac_users():
    from backend.security.rbac import get_rbac
    return {"ok": True, "users": get_rbac().list_users()}


@router.post("/security/rbac/users")
async def create_rbac_user(request: Request):
    from backend.security.rbac import get_rbac
    body = await request.json()
    user = get_rbac().create_user(
        user_id=body["user_id"], name=body["name"], role=body.get("role", "viewer"),
    )
    return {"ok": True, "user": user}


@router.put("/security/rbac/users/{user_id}/role")
async def update_rbac_role(user_id: str, request: Request):
    from backend.security.rbac import get_rbac
    body = await request.json()
    ok = get_rbac().update_user_role(user_id, body["role"])
    if not ok:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return {"ok": True}


@router.delete("/security/rbac/users/{user_id}")
async def deactivate_rbac_user(user_id: str):
    from backend.security.rbac import get_rbac
    ok = get_rbac().deactivate_user(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return {"ok": True}


@router.get("/security/rbac/policies")
async def list_rbac_policies():
    from backend.security.rbac import get_rbac
    return {"ok": True, "policies": get_rbac().list_policies()}


@router.post("/security/rbac/policies")
async def add_rbac_policy(request: Request):
    from backend.security.rbac import get_rbac, PolicyRule, PolicyEffect
    body = await request.json()
    rule = PolicyRule(
        rule_id=body["rule_id"],
        effect=PolicyEffect(body["effect"]),
        action_patterns=body.get("action_patterns", []),
        modes=body.get("modes", []),
        skills=body.get("skills", []),
        roles=body.get("roles", []),
        conditions=body.get("conditions", {}),
        description=body.get("description", ""),
    )
    get_rbac().add_policy(rule)
    return {"ok": True}


@router.delete("/security/rbac/policies/{rule_id}")
async def remove_rbac_policy(rule_id: str):
    from backend.security.rbac import get_rbac
    ok = get_rbac().remove_policy(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Política no encontrada")
    return {"ok": True}


# ── Audit ────────────────────────────────────────────────────────────────

@router.get("/security/audit")
async def query_audit(
    category: str | None = None,
    severity: str | None = None,
    actor: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    from backend.security.audit import get_audit
    entries = get_audit().query(
        category=category, severity=severity, actor=actor,
        limit=limit, offset=offset,
    )
    return {"ok": True, "entries": entries, "count": len(entries)}


@router.get("/security/audit/stats")
async def audit_stats(hours: int = 24):
    from backend.security.audit import get_audit
    return {"ok": True, "stats": get_audit().get_stats(hours)}


@router.get("/security/audit/export/{fmt}")
async def export_audit(fmt: str, category: str | None = None, severity: str | None = None):
    from backend.security.audit import get_audit
    from fastapi.responses import PlainTextResponse
    audit = get_audit()
    filters: dict = {}
    if category:
        filters["category"] = category
    if severity:
        filters["severity"] = severity
    if fmt == "json":
        return PlainTextResponse(audit.export_json(**filters), media_type="application/json")
    elif fmt == "csv":
        return PlainTextResponse(audit.export_csv(**filters), media_type="text/csv")
    raise HTTPException(status_code=400, detail="Formato inválido: use json o csv")


# ── Ethical ──────────────────────────────────────────────────────────────

@router.get("/security/ethical/restrictions")
async def list_ethical_restrictions():
    from backend.security.ethical import get_ethical_engine
    return {"ok": True, "restrictions": get_ethical_engine().list_restrictions()}


@router.put("/security/ethical/override/{rule_id}")
async def toggle_ethical_override(rule_id: str, request: Request):
    from backend.security.ethical import get_ethical_engine
    body = await request.json()
    try:
        get_ethical_engine().set_configurable_override(rule_id, body.get("blocked", True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


# ── Sandbox ──────────────────────────────────────────────────────────────

@router.get("/security/sandbox/status")
async def sandbox_status():
    from backend.security.sandbox import get_sandbox
    return {"ok": True, "status": get_sandbox().get_status()}


@router.post("/security/sandbox/execute")
async def sandbox_execute(request: Request):
    from backend.security.sandbox import get_sandbox
    from backend.security.rate_limiter import get_rate_limiter
    rl = get_rate_limiter().check_sandbox("local_owner")
    if not rl.allowed:
        raise HTTPException(status_code=429, detail=f"Rate limit: retry after {rl.reset_after_seconds:.0f}s")
    body = await request.json()
    lang = body.get("language", "python")
    code = body.get("code", "")
    if not code:
        raise HTTPException(status_code=400, detail="No code provided")
    sandbox = get_sandbox()
    if lang == "python":
        result = await sandbox.execute_python(code)
    elif lang == "shell":
        result = await sandbox.execute_shell(code)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {lang}")
    return {
        "ok": True,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration_ms": result.duration_ms,
        "timed_out": result.timed_out,
        "sandbox_type": result.sandbox_type,
    }


# ── Rate Limiter ─────────────────────────────────────────────────────────

@router.get("/security/rate-limits/status")
async def rate_limits_status():
    from backend.security.rate_limiter import get_rate_limiter
    return {"ok": True, "status": get_rate_limiter().get_status()}


# ── Injection Detection ─────────────────────────────────────────────────

@router.post("/security/injection/scan")
async def scan_for_injection(request: Request):
    from backend.security.injection_detector import get_injection_detector
    body = await request.json()
    text = body.get("text", "")
    source = body.get("source", "external")
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")
    should_block, result = get_injection_detector().scan_and_block(text, source)
    return {
        "ok": True,
        "detected": result.detected,
        "confidence": result.confidence,
        "severity": result.severity,
        "should_block": should_block,
        "patterns_matched": result.patterns_matched,
    }


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 11 — Memory / Knowledge Graph
# ═══════════════════════════════════════════════════════════════════════════

# ── Long-Term Memory ─────────────────────────────────────────────────────

@router.get("/memory/ltm")
async def list_ltm(category: str | None = None, limit: int = 50):
    from backend.core.memory_ltm import get_ltm
    return {"ok": True, "memories": get_ltm().list_memories(category=category, limit=limit)}


@router.post("/memory/ltm")
async def store_ltm(request: Request):
    from backend.core.memory_ltm import get_ltm
    body = await request.json()
    memory_id = get_ltm().store(
        content=body["content"],
        category=body.get("category", "fact"),
        importance=body.get("importance", 0.5),
        metadata=body.get("metadata"),
    )
    return {"ok": True, "memory_id": memory_id}


@router.post("/memory/ltm/search")
async def search_ltm(request: Request):
    from backend.core.memory_ltm import get_ltm
    body = await request.json()
    results = get_ltm().search(
        query=body["query"],
        top_k=body.get("top_k", 5),
        category=body.get("category"),
    )
    return {"ok": True, "results": results}


@router.delete("/memory/ltm/{memory_id}")
async def delete_ltm(memory_id: str):
    from backend.core.memory_ltm import get_ltm
    ok = get_ltm().delete(memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Memoria no encontrada")
    return {"ok": True}


@router.put("/memory/ltm/{memory_id}/importance")
async def update_ltm_importance(memory_id: str, request: Request):
    from backend.core.memory_ltm import get_ltm
    body = await request.json()
    ok = get_ltm().update_importance(memory_id, body["importance"])
    if not ok:
        raise HTTPException(status_code=404, detail="Memoria no encontrada")
    return {"ok": True}


# ── Knowledge Graph ──────────────────────────────────────────────────────

@router.get("/memory/kg/stats")
async def kg_stats():
    from backend.core.knowledge_graph import get_knowledge_graph
    return {"ok": True, "stats": get_knowledge_graph().get_stats()}


@router.get("/memory/kg/entities")
async def list_kg_entities(entity_type: str | None = None, limit: int = 100):
    from backend.core.knowledge_graph import get_knowledge_graph
    return {"ok": True, "entities": get_knowledge_graph().list_entities(entity_type=entity_type, limit=limit)}


@router.post("/memory/kg/entities")
async def add_kg_entity(request: Request):
    from backend.core.knowledge_graph import get_knowledge_graph
    body = await request.json()
    entity = get_knowledge_graph().add_entity(
        entity_id=body["entity_id"],
        entity_type=body["entity_type"],
        name=body["name"],
        properties=body.get("properties"),
    )
    return {"ok": True, "entity": {"entity_id": entity.entity_id, "name": entity.name, "entity_type": entity.entity_type}}


@router.delete("/memory/kg/entities/{entity_id}")
async def delete_kg_entity(entity_id: str):
    from backend.core.knowledge_graph import get_knowledge_graph
    ok = get_knowledge_graph().delete_entity(entity_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Entidad no encontrada")
    return {"ok": True}


@router.get("/memory/kg/entities/{entity_id}/relations")
async def get_kg_relations(entity_id: str, direction: str = "both"):
    from backend.core.knowledge_graph import get_knowledge_graph
    return {"ok": True, "relations": get_knowledge_graph().get_relations(entity_id, direction)}


@router.post("/memory/kg/relations")
async def add_kg_relation(request: Request):
    from backend.core.knowledge_graph import get_knowledge_graph
    body = await request.json()
    rel = get_knowledge_graph().add_relation(
        source_id=body["source_id"],
        target_id=body["target_id"],
        relation_type=body["relation_type"],
        confidence=body.get("confidence", 1.0),
        properties=body.get("properties"),
    )
    if not rel:
        raise HTTPException(status_code=400, detail="Relación inválida")
    return {"ok": True}


@router.get("/memory/kg/entities/{entity_id}/neighbors")
async def get_kg_neighbors(entity_id: str, depth: int = 1):
    from backend.core.knowledge_graph import get_knowledge_graph
    return {"ok": True, "graph": get_knowledge_graph().get_neighbors(entity_id, depth)}


@router.get("/memory/kg/path/{source_id}/{target_id}")
async def find_kg_path(source_id: str, target_id: str):
    from backend.core.knowledge_graph import get_knowledge_graph
    path = get_knowledge_graph().find_path(source_id, target_id)
    return {"ok": True, "path": path}


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 12 — RPA / Macros / Rollback
# ═══════════════════════════════════════════════════════════════════════════

# ── Macros ───────────────────────────────────────────────────────────────

@router.get("/macros")
async def list_macros(status: str | None = None):
    from backend.core.macro_engine import get_macro_engine
    return {"ok": True, "macros": get_macro_engine().list_macros(status=status)}


@router.post("/macros")
async def create_macro(request: Request):
    from backend.core.macro_engine import get_macro_engine
    body = await request.json()
    macro = get_macro_engine().import_macro(body)
    return {"ok": True, "macro": macro.to_dict()}


@router.get("/macros/{macro_id}")
async def get_macro(macro_id: str):
    from backend.core.macro_engine import get_macro_engine
    macro = get_macro_engine().get_macro(macro_id)
    if not macro:
        raise HTTPException(status_code=404, detail="Macro no encontrada")
    return {"ok": True, "macro": macro.to_dict()}


@router.put("/macros/{macro_id}")
async def update_macro(macro_id: str, request: Request):
    from backend.core.macro_engine import get_macro_engine
    body = await request.json()
    ok = get_macro_engine().update_macro(macro_id, body)
    if not ok:
        raise HTTPException(status_code=404, detail="Macro no encontrada")
    return {"ok": True}


@router.delete("/macros/{macro_id}")
async def delete_macro(macro_id: str):
    from backend.core.macro_engine import get_macro_engine
    ok = get_macro_engine().delete_macro(macro_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Macro no encontrada")
    return {"ok": True}


@router.post("/macros/{macro_id}/run")
async def run_macro(macro_id: str, request: Request):
    from backend.core.macro_engine import get_macro_engine
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    result = await get_macro_engine().execute_macro(macro_id, variables=body.get("variables"))
    return {"ok": "error" not in result, **result}


@router.get("/macros/{macro_id}/runs")
async def get_macro_runs(macro_id: str):
    from backend.core.macro_engine import get_macro_engine
    return {"ok": True, "runs": get_macro_engine().get_runs(macro_id)}


@router.get("/macros/{macro_id}/export")
async def export_macro(macro_id: str):
    from backend.core.macro_engine import get_macro_engine
    data = get_macro_engine().export_macro(macro_id)
    if not data:
        raise HTTPException(status_code=404, detail="Macro no encontrada")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(data, media_type="application/json")


@router.post("/macros/recording/start")
async def start_recording(request: Request):
    from backend.core.macro_engine import get_macro_engine
    body = await request.json()
    session_id = body.get("session_id", "default")
    macro_id = get_macro_engine().start_recording(session_id)
    return {"ok": True, "macro_id": macro_id, "session_id": session_id}


@router.post("/macros/recording/stop")
async def stop_recording(request: Request):
    from backend.core.macro_engine import get_macro_engine
    body = await request.json()
    session_id = body.get("session_id", "default")
    macro = get_macro_engine().stop_recording(
        session_id, name=body.get("name", ""), description=body.get("description", ""),
    )
    if not macro:
        raise HTTPException(status_code=400, detail="No recording found for session")
    return {"ok": True, "macro": macro.to_dict()}


# ── Rollback ─────────────────────────────────────────────────────────────

@router.get("/rollback/snapshots")
async def list_snapshots():
    from backend.core.rollback import get_rollback
    return {"ok": True, "snapshots": get_rollback().list_snapshots()}


@router.get("/rollback/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str):
    from backend.core.rollback import get_rollback
    snap = get_rollback().get_snapshot(snapshot_id)
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot no encontrado")
    return {"ok": True, "snapshot": snap}


@router.post("/rollback/snapshots/{snapshot_id}/restore")
async def restore_snapshot(snapshot_id: str):
    from backend.core.rollback import get_rollback
    result = get_rollback().rollback(snapshot_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Rollback failed"))
    return result


@router.delete("/rollback/snapshots/{snapshot_id}")
async def delete_snapshot(snapshot_id: str):
    from backend.core.rollback import get_rollback
    ok = get_rollback().delete_snapshot(snapshot_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Snapshot no encontrado")
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════
# Phase 13 — DAG Planner
# ═══════════════════════════════════════════════════════════════════════

@router.post("/dag/create")
async def create_dag(body: dict):
    from backend.core.dag_executor import get_dag_executor
    dag = get_dag_executor().create_dag(
        name=body["name"],
        nodes=body["nodes"],
        description=body.get("description", ""),
    )
    return {"ok": True, "dag": dag.to_dict()}


@router.get("/dag/list")
async def list_dags(status: str = None, limit: int = 50):
    from backend.core.dag_executor import get_dag_executor
    return {"ok": True, "dags": get_dag_executor().list_dags(status=status, limit=limit)}


@router.get("/dag/{dag_id}")
async def get_dag(dag_id: str):
    from backend.core.dag_executor import get_dag_executor
    dag = get_dag_executor().get_dag(dag_id)
    if not dag:
        raise HTTPException(status_code=404, detail="DAG no encontrado")
    return {"ok": True, "dag": dag.to_dict()}


@router.post("/dag/{dag_id}/execute")
async def execute_dag(dag_id: str):
    from backend.core.dag_executor import get_dag_executor
    dag = await get_dag_executor().execute(dag_id)
    return {"ok": True, "dag": dag.to_dict()}


@router.post("/dag/{dag_id}/resume")
async def resume_dag(dag_id: str):
    from backend.core.dag_executor import get_dag_executor
    dag = await get_dag_executor().resume(dag_id)
    return {"ok": True, "dag": dag.to_dict()}


@router.delete("/dag/{dag_id}")
async def delete_dag(dag_id: str):
    from backend.core.dag_executor import get_dag_executor
    ok = get_dag_executor().delete_dag(dag_id)
    if not ok:
        raise HTTPException(status_code=404, detail="DAG no encontrado")
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════
# Phase 13 — Goal Engine
# ═══════════════════════════════════════════════════════════════════════

@router.post("/goals")
async def create_goal(body: dict):
    from backend.core.goal_engine import get_goal_engine
    goal = get_goal_engine().create_goal(
        title=body["title"],
        description=body.get("description", ""),
        deadline=body.get("deadline"),
        kpis=body.get("kpis"),
        sub_tasks=body.get("sub_tasks"),
        tags=body.get("tags"),
    )
    return {"ok": True, "goal": goal.to_dict()}


@router.get("/goals")
async def list_goals(status: str = None, tag: str = None, limit: int = 50):
    from backend.core.goal_engine import get_goal_engine
    return {"ok": True, "goals": get_goal_engine().list_goals(status=status, tag=tag, limit=limit)}


@router.get("/goals/{goal_id}")
async def get_goal(goal_id: str):
    from backend.core.goal_engine import get_goal_engine
    goal = get_goal_engine().get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Objetivo no encontrado")
    return {"ok": True, "goal": goal.to_dict()}


@router.delete("/goals/{goal_id}")
async def delete_goal(goal_id: str):
    from backend.core.goal_engine import get_goal_engine
    ok = get_goal_engine().delete_goal(goal_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Objetivo no encontrado")
    return {"ok": True}


@router.put("/goals/{goal_id}/kpi/{kpi_id}")
async def update_goal_kpi(goal_id: str, kpi_id: str, body: dict):
    from backend.core.goal_engine import get_goal_engine
    goal = get_goal_engine().update_kpi(goal_id, kpi_id, body["value"])
    if not goal:
        raise HTTPException(status_code=404, detail="Objetivo o KPI no encontrado")
    return {"ok": True, "goal": goal.to_dict()}


@router.post("/goals/{goal_id}/tasks")
async def add_goal_task(goal_id: str, body: dict):
    from backend.core.goal_engine import get_goal_engine
    goal = get_goal_engine().add_sub_task(goal_id, body["title"], body.get("dag_id"))
    if not goal:
        raise HTTPException(status_code=404, detail="Objetivo no encontrado")
    return {"ok": True, "goal": goal.to_dict()}


@router.put("/goals/{goal_id}/tasks/{task_id}")
async def update_goal_task(goal_id: str, task_id: str, body: dict):
    from backend.core.goal_engine import get_goal_engine
    goal = get_goal_engine().update_sub_task(goal_id, task_id, body["status"])
    if not goal:
        raise HTTPException(status_code=404, detail="Objetivo o tarea no encontrado")
    return {"ok": True, "goal": goal.to_dict()}


@router.get("/goals/{goal_id}/deviation")
async def check_goal_deviation(goal_id: str):
    from backend.core.goal_engine import get_goal_engine
    result = get_goal_engine().check_deviation(goal_id)
    if not result:
        raise HTTPException(status_code=404, detail="Objetivo no encontrado")
    return {"ok": True, "deviation": result}


@router.post("/goals/{goal_id}/replan")
async def replan_goal(goal_id: str):
    from backend.core.goal_engine import get_goal_engine
    goal = get_goal_engine().trigger_replan(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Objetivo no encontrado")
    return {"ok": True, "goal": goal.to_dict()}


@router.post("/goals/{goal_id}/activate")
async def activate_goal(goal_id: str):
    from backend.core.goal_engine import get_goal_engine
    goal = get_goal_engine().activate_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Objetivo no encontrado")
    return {"ok": True, "goal": goal.to_dict()}


# ═══════════════════════════════════════════════════════════════════════
# Phase 13 — Analytics
# ═══════════════════════════════════════════════════════════════════════

@router.post("/analytics/record")
async def record_task_metric(body: dict):
    from backend.core.analytics import get_analytics
    get_analytics().record_task(
        task_id=body["task_id"],
        task_type=body.get("task_type", "general"),
        status=body.get("status", "completed"),
        duration_seconds=body.get("duration_seconds", 0),
        tokens_in=body.get("tokens_in", 0),
        tokens_out=body.get("tokens_out", 0),
        provider=body.get("provider", ""),
        model=body.get("model", ""),
        error=body.get("error"),
    )
    return {"ok": True}


@router.post("/analytics/tokens")
async def record_token_usage(body: dict):
    from backend.core.analytics import get_analytics
    get_analytics().record_token_usage(
        provider=body["provider"],
        model=body["model"],
        tokens_in=body.get("tokens_in", 0),
        tokens_out=body.get("tokens_out", 0),
        cost_usd=body.get("cost_usd", 0),
    )
    return {"ok": True}


@router.get("/analytics/tasks")
async def get_task_stats(hours: int = 0):
    from backend.core.analytics import get_analytics
    import time
    since = time.time() - (hours * 3600) if hours > 0 else None
    return {"ok": True, "stats": get_analytics().get_task_stats(since)}


@router.get("/analytics/tokens")
async def get_token_stats(hours: int = 0):
    from backend.core.analytics import get_analytics
    import time
    since = time.time() - (hours * 3600) if hours > 0 else None
    return {"ok": True, "stats": get_analytics().get_token_stats(since)}


@router.get("/analytics/errors")
async def get_error_history(limit: int = 50, hours: int = 0):
    from backend.core.analytics import get_analytics
    import time
    since = time.time() - (hours * 3600) if hours > 0 else None
    return {"ok": True, "errors": get_analytics().get_error_history(limit, since)}


@router.get("/analytics/time-distribution")
async def get_time_distribution(hours: int = 0):
    from backend.core.analytics import get_analytics
    import time
    since = time.time() - (hours * 3600) if hours > 0 else None
    return {"ok": True, "distribution": get_analytics().get_time_distribution(since)}


@router.get("/analytics/timeline")
async def get_activity_timeline(hours: int = 24):
    from backend.core.analytics import get_analytics
    return {"ok": True, "timeline": get_analytics().get_activity_timeline(hours)}


@router.get("/analytics/dashboard")
async def get_executive_dashboard():
    from backend.core.analytics import get_analytics
    return {"ok": True, "dashboard": get_analytics().get_dashboard()}


@router.get("/analytics/report/weekly")
async def get_weekly_report():
    from backend.core.analytics import get_analytics
    return {"ok": True, "report": get_analytics().generate_weekly_report()}


# ═══════════════════════════════════════════════════════════════════════
# Phase 14 — Event Bus
# ═══════════════════════════════════════════════════════════════════════

@router.post("/events/emit")
async def emit_event(body: dict):
    from backend.core.event_bus import get_event_bus
    event = await get_event_bus().emit(
        event_type=body["event_type"],
        payload=body.get("payload", {}),
        source=body.get("source", "api"),
        session_id=body.get("session_id", ""),
    )
    return {"ok": True, "event": event.to_dict()}


@router.get("/events")
async def list_events(event_type: str = None, source: str = None, limit: int = 50):
    from backend.core.event_bus import get_event_bus
    return {"ok": True, "events": get_event_bus().get_events(event_type=event_type, source=source, limit=limit)}


@router.get("/events/stats")
async def get_event_stats():
    from backend.core.event_bus import get_event_bus
    return {"ok": True, "stats": get_event_bus().get_stats()}


@router.post("/events/process-pending")
async def process_pending_events():
    from backend.core.event_bus import get_event_bus
    count = await get_event_bus().process_pending()
    return {"ok": True, "processed": count}


# Webhook listener for external integrations (Zapier/n8n/Make)
@router.post("/webhooks/incoming/{webhook_type}")
async def incoming_webhook(webhook_type: str, body: dict):
    from backend.core.event_bus import get_event_bus
    event = await get_event_bus().emit(
        event_type=f"webhook.{webhook_type}",
        payload=body,
        source="webhook",
    )
    return {"ok": True, "event_id": event.event_id}


# ═══════════════════════════════════════════════════════════════════════
# Phase 14 — Self-Healing
# ═══════════════════════════════════════════════════════════════════════

@router.get("/self-healing/stats")
async def get_self_healing_stats():
    from backend.core.self_healing import get_self_healing
    return {"ok": True, "stats": get_self_healing().get_stats()}


@router.post("/self-healing/reset-stats")
async def reset_self_healing_stats():
    from backend.core.self_healing import get_self_healing
    get_self_healing().reset_stats()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════
# Phase 14 — Artifact Versioning
# ═══════════════════════════════════════════════════════════════════════

@router.post("/versioning/create")
async def create_artifact_version(body: dict):
    from backend.core.artifact_versioning import get_versioning
    result = get_versioning().create_version(
        artifact_id=body["artifact_id"],
        artifact_type=body["artifact_type"],
        content=body["content"],
        author=body.get("author", "system"),
        message=body.get("message", ""),
        stage=body.get("stage", "draft"),
    )
    return {"ok": True, "version": result}


@router.get("/versioning/list")
async def list_artifact_versions(artifact_id: str = None, artifact_type: str = None, stage: str = None, limit: int = 50):
    from backend.core.artifact_versioning import get_versioning
    return {"ok": True, "versions": get_versioning().list_versions(artifact_id=artifact_id, artifact_type=artifact_type, stage=stage, limit=limit)}


@router.get("/versioning/{version_id}")
async def get_artifact_version(version_id: str):
    from backend.core.artifact_versioning import get_versioning
    v = get_versioning().get_version(version_id)
    if not v:
        raise HTTPException(status_code=404, detail="Versión no encontrada")
    return {"ok": True, "version": v}


@router.get("/versioning/latest/{artifact_id}")
async def get_latest_version(artifact_id: str, stage: str = None):
    from backend.core.artifact_versioning import get_versioning
    v = get_versioning().get_latest(artifact_id, stage)
    if not v:
        raise HTTPException(status_code=404, detail="Artefacto no encontrado")
    return {"ok": True, "version": v}


@router.post("/versioning/{version_id}/promote")
async def promote_version(version_id: str, body: dict):
    from backend.core.artifact_versioning import get_versioning
    result = get_versioning().promote(version_id, body["target_stage"])
    if not result:
        raise HTTPException(status_code=404, detail="Versión no encontrada")
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"ok": True, **result}


@router.post("/versioning/{artifact_id}/rollback")
async def rollback_artifact(artifact_id: str, body: dict):
    from backend.core.artifact_versioning import get_versioning
    result = get_versioning().rollback_to(artifact_id, body["version"])
    if not result:
        raise HTTPException(status_code=404, detail="Versión no encontrada")
    return {"ok": True, "version": result}


@router.get("/versioning/{artifact_id}/diff")
async def diff_versions(artifact_id: str, v1: int = 1, v2: int = 2):
    from backend.core.artifact_versioning import get_versioning
    result = get_versioning().diff_versions(artifact_id, v1, v2)
    if not result:
        raise HTTPException(status_code=404, detail="Versiones no encontradas")
    return {"ok": True, "diff": result}


# ═══════════════════════════════════════════════════════════════════════
# Phase 15 — ETL Pipelines
# ═══════════════════════════════════════════════════════════════════════

@router.post("/etl/pipelines")
async def create_etl_pipeline(body: dict):
    from backend.core.etl_engine import get_etl
    pipeline = get_etl().create_pipeline(
        name=body["name"],
        steps=body["steps"],
        description=body.get("description", ""),
        schedule=body.get("schedule"),
    )
    return {"ok": True, "pipeline": pipeline.to_dict()}


@router.get("/etl/pipelines")
async def list_etl_pipelines(limit: int = 50):
    from backend.core.etl_engine import get_etl
    return {"ok": True, "pipelines": get_etl().list_pipelines(limit)}


@router.get("/etl/pipelines/{pipeline_id}")
async def get_etl_pipeline(pipeline_id: str):
    from backend.core.etl_engine import get_etl
    p = get_etl().get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(status_code=404, detail="Pipeline no encontrado")
    return {"ok": True, "pipeline": p.to_dict()}


@router.post("/etl/pipelines/{pipeline_id}/run")
async def run_etl_pipeline(pipeline_id: str):
    from backend.core.etl_engine import get_etl
    result = await get_etl().run_pipeline(pipeline_id)
    return result


@router.get("/etl/pipelines/{pipeline_id}/runs")
async def get_etl_runs(pipeline_id: str, limit: int = 20):
    from backend.core.etl_engine import get_etl
    return {"ok": True, "runs": get_etl().get_runs(pipeline_id, limit)}


@router.delete("/etl/pipelines/{pipeline_id}")
async def delete_etl_pipeline(pipeline_id: str):
    from backend.core.etl_engine import get_etl
    ok = get_etl().delete_pipeline(pipeline_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Pipeline no encontrado")
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════
# Phase 15 — Causal Alerts
# ═══════════════════════════════════════════════════════════════════════

@router.post("/alerts/metrics")
async def record_metric(body: dict):
    from backend.core.causal_alerts import get_alert_engine
    get_alert_engine().record_metric(body["metric_name"], body["value"])
    return {"ok": True}


@router.get("/alerts/metrics/{metric_name}")
async def get_metric_history(metric_name: str, window: int = 3600):
    from backend.core.causal_alerts import get_alert_engine
    return {"ok": True, "history": get_alert_engine().get_metric_history(metric_name, window)}


@router.post("/alerts/rules")
async def create_alert_rule(body: dict):
    from backend.core.causal_alerts import get_alert_engine
    rule = get_alert_engine().create_rule(
        name=body["name"],
        metric_name=body["metric_name"],
        condition=body["condition"],
        threshold=body["threshold"],
        window_seconds=body.get("window_seconds", 300),
        action=body.get("action", "notify"),
        confidence_min=body.get("confidence_min", 0.85),
    )
    return {"ok": True, "rule": rule.to_dict()}


@router.get("/alerts/rules")
async def list_alert_rules():
    from backend.core.causal_alerts import get_alert_engine
    return {"ok": True, "rules": get_alert_engine().list_rules()}


@router.delete("/alerts/rules/{rule_id}")
async def delete_alert_rule(rule_id: str):
    from backend.core.causal_alerts import get_alert_engine
    ok = get_alert_engine().delete_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Regla no encontrada")
    return {"ok": True}


@router.post("/alerts/evaluate")
async def evaluate_alert_rules():
    from backend.core.causal_alerts import get_alert_engine
    triggered = get_alert_engine().evaluate_rules()
    return {"ok": True, "triggered": [a.to_dict() for a in triggered]}


@router.get("/alerts/events")
async def get_alert_events(resolved: bool = None, limit: int = 50):
    from backend.core.causal_alerts import get_alert_engine
    return {"ok": True, "alerts": get_alert_engine().get_alerts(resolved=resolved, limit=limit)}


@router.post("/alerts/events/{alert_id}/resolve")
async def resolve_alert(alert_id: str):
    from backend.core.causal_alerts import get_alert_engine
    ok = get_alert_engine().resolve_alert(alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Alerta no encontrada")
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════
# Phase 15 — RLHF Lite
# ═══════════════════════════════════════════════════════════════════════

@router.post("/rlhf/signal")
async def record_rlhf_signal(body: dict):
    from backend.core.rlhf_lite import get_rlhf
    result = get_rlhf().record_signal(
        signal_name=body["signal_name"],
        user_id=body.get("user_id", "default"),
        context=body.get("context"),
    )
    return {"ok": True, **result}


@router.get("/rlhf/preferences")
async def get_rlhf_preferences(user_id: str = "default"):
    from backend.core.rlhf_lite import get_rlhf
    return {"ok": True, "preferences": get_rlhf().get_preferences(user_id)}


@router.get("/rlhf/profile")
async def get_rlhf_profile(user_id: str = "default"):
    from backend.core.rlhf_lite import get_rlhf
    return {"ok": True, "profile": get_rlhf().get_profile_summary(user_id)}


@router.delete("/rlhf/preferences/{pref_key}")
async def delete_rlhf_preference(pref_key: str, user_id: str = "default"):
    from backend.core.rlhf_lite import get_rlhf
    ok = get_rlhf().delete_preference(user_id, pref_key)
    if not ok:
        raise HTTPException(status_code=404, detail="Preferencia no encontrada")
    return {"ok": True}


@router.get("/rlhf/signals")
async def get_rlhf_signal_history(user_id: str = "default", limit: int = 50):
    from backend.core.rlhf_lite import get_rlhf
    return {"ok": True, "signals": get_rlhf().get_signal_history(user_id, limit)}


@router.get("/rlhf/context-injection")
async def get_rlhf_context(user_id: str = "default"):
    from backend.core.rlhf_lite import get_rlhf
    return {"ok": True, "injection": get_rlhf().get_context_injection(user_id)}


# ═══════════════════════════════════════════════════════════════════════
# Phase 16 — Autonomous Agents
# ═══════════════════════════════════════════════════════════════════════

@router.get("/agents/types")
async def get_agent_types():
    from backend.core.autonomous_agents import get_agent_manager
    return {"ok": True, "agents": get_agent_manager().get_available_agents()}


@router.post("/agents/tasks")
async def create_agent_task(body: dict):
    from backend.core.autonomous_agents import get_agent_manager
    task = get_agent_manager().create_task(
        agent_type=body["agent_type"],
        title=body["title"],
        params=body.get("params"),
    )
    return {"ok": True, "task": task.to_dict()}


@router.get("/agents/tasks")
async def list_agent_tasks(agent_type: str = None, status: str = None, limit: int = 50):
    from backend.core.autonomous_agents import get_agent_manager
    return {"ok": True, "tasks": get_agent_manager().list_tasks(agent_type=agent_type, status=status, limit=limit)}


@router.get("/agents/tasks/{task_id}")
async def get_agent_task(task_id: str):
    from backend.core.autonomous_agents import get_agent_manager
    task = get_agent_manager().get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return {"ok": True, "task": task.to_dict()}


@router.put("/agents/tasks/{task_id}")
async def update_agent_task(task_id: str, body: dict):
    from backend.core.autonomous_agents import get_agent_manager
    task = get_agent_manager().update_task_status(
        task_id, body["status"], body.get("result"), body.get("error"),
    )
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return {"ok": True, "task": task.to_dict()}


@router.delete("/agents/tasks/{task_id}")
async def delete_agent_task(task_id: str):
    from backend.core.autonomous_agents import get_agent_manager
    ok = get_agent_manager().delete_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return {"ok": True}


# ── SOPs ─────────────────────────────────────────────────────────────

@router.post("/agents/sops")
async def create_sop(body: dict):
    from backend.core.autonomous_agents import get_sop_generator
    sop = get_sop_generator().create_sop(
        title=body["title"],
        content=body["content"],
        task_pattern=body.get("task_pattern", ""),
        sop_format=body.get("format", "markdown"),
    )
    return {"ok": True, "sop": sop}


@router.get("/agents/sops")
async def list_sops(limit: int = 50):
    from backend.core.autonomous_agents import get_sop_generator
    return {"ok": True, "sops": get_sop_generator().list_sops(limit)}


@router.get("/agents/sops/{sop_id}")
async def get_sop(sop_id: str):
    from backend.core.autonomous_agents import get_sop_generator
    sop = get_sop_generator().get_sop(sop_id)
    if not sop:
        raise HTTPException(status_code=404, detail="SOP no encontrado")
    return {"ok": True, "sop": sop}


@router.put("/agents/sops/{sop_id}")
async def update_sop(sop_id: str, body: dict):
    from backend.core.autonomous_agents import get_sop_generator
    ok = get_sop_generator().update_sop(sop_id, body["content"])
    if not ok:
        raise HTTPException(status_code=404, detail="SOP no encontrado")
    return {"ok": True}


@router.delete("/agents/sops/{sop_id}")
async def delete_sop(sop_id: str):
    from backend.core.autonomous_agents import get_sop_generator
    ok = get_sop_generator().delete_sop(sop_id)
    if not ok:
        raise HTTPException(status_code=404, detail="SOP no encontrado")
    return {"ok": True}


# ── A/B Testing ──────────────────────────────────────────────────────

@router.post("/agents/ab/experiments")
async def create_ab_experiment(body: dict):
    from backend.core.autonomous_agents import get_ab_engine
    result = get_ab_engine().create_experiment(
        name=body["name"],
        element=body["element"],
        control=body["control"],
        variant=body["variant"],
        metric=body["metric"],
    )
    return {"ok": True, "experiment": result}


@router.get("/agents/ab/experiments")
async def list_ab_experiments(status: str = None, limit: int = 50):
    from backend.core.autonomous_agents import get_ab_engine
    return {"ok": True, "experiments": get_ab_engine().list_experiments(status, limit)}


@router.get("/agents/ab/experiments/{experiment_id}")
async def get_ab_experiment(experiment_id: str):
    from backend.core.autonomous_agents import get_ab_engine
    exp = get_ab_engine().get_experiment(experiment_id)
    if not exp:
        raise HTTPException(status_code=404, detail="Experimento no encontrado")
    return {"ok": True, "experiment": exp}


@router.post("/agents/ab/experiments/{experiment_id}/result")
async def record_ab_result(experiment_id: str, body: dict):
    from backend.core.autonomous_agents import get_ab_engine
    ok = get_ab_engine().record_result(experiment_id, body["group"], body["value"])
    if not ok:
        raise HTTPException(status_code=404, detail="Experimento no encontrado")
    return {"ok": True}


@router.post("/agents/ab/experiments/{experiment_id}/evaluate")
async def evaluate_ab_experiment(experiment_id: str):
    from backend.core.autonomous_agents import get_ab_engine
    result = get_ab_engine().evaluate_experiment(experiment_id)
    if not result:
        raise HTTPException(status_code=404, detail="Experimento no encontrado")
    return {"ok": True, "evaluation": result}


# ── What-If Simulations ─────────────────────────────────────────────

@router.post("/agents/simulations")
async def create_simulation(body: dict):
    from backend.core.autonomous_agents import get_whatif
    sim = get_whatif().create_simulation(
        name=body["name"],
        sim_type=body["sim_type"],
        params=body.get("params", {}),
    )
    return {"ok": True, "simulation": sim}


@router.get("/agents/simulations")
async def list_simulations(limit: int = 50):
    from backend.core.autonomous_agents import get_whatif
    return {"ok": True, "simulations": get_whatif().list_simulations(limit)}


@router.get("/agents/simulations/{sim_id}")
async def get_simulation(sim_id: str):
    from backend.core.autonomous_agents import get_whatif
    sim = get_whatif().get_simulation(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="Simulación no encontrada")
    return {"ok": True, "simulation": sim}


@router.post("/agents/simulations/{sim_id}/run")
async def run_simulation(sim_id: str):
    from backend.core.autonomous_agents import get_whatif
    result = get_whatif().run_simulation(sim_id)
    if not result:
        raise HTTPException(status_code=404, detail="Simulación no encontrada")
    return {"ok": True, "result": result}


# ═══════════════════════════════════════════════════════════════════════
# Phase 17 — Productivity Suite
# ═══════════════════════════════════════════════════════════════════════

# ── Clipboard ────────────────────────────────────────────────────────

@router.post("/clipboard")
async def add_to_clipboard(body: dict):
    from backend.core.productivity import get_clipboard
    result = get_clipboard().add(
        content=body["content"],
        content_type=body.get("content_type", "text"),
        source=body.get("source", ""),
    )
    return {"ok": True, **result}


@router.get("/clipboard")
async def get_clipboard_history(limit: int = 50, search: str = None):
    from backend.core.productivity import get_clipboard
    return {"ok": True, "history": get_clipboard().get_history(limit, search)}


@router.put("/clipboard/{clip_id}/pin")
async def pin_clipboard_item(clip_id: int, body: dict):
    from backend.core.productivity import get_clipboard
    ok = get_clipboard().pin(clip_id, body.get("pinned", True))
    if not ok:
        raise HTTPException(status_code=404, detail="Item no encontrado")
    return {"ok": True}


@router.delete("/clipboard/{clip_id}")
async def delete_clipboard_item(clip_id: int):
    from backend.core.productivity import get_clipboard
    ok = get_clipboard().delete(clip_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Item no encontrado")
    return {"ok": True}


@router.post("/clipboard/clear")
async def clear_clipboard():
    from backend.core.productivity import get_clipboard
    count = get_clipboard().clear_unpinned()
    return {"ok": True, "cleared": count}


# ── Notifications ────────────────────────────────────────────────────

@router.post("/notifications")
async def send_notification(body: dict):
    from backend.core.productivity import get_notifications
    result = get_notifications().send(
        title=body["title"],
        body=body.get("body", ""),
        channel=body.get("channel", "system"),
        priority=body.get("priority", 5),
        action_url=body.get("action_url"),
    )
    return {"ok": True, **result}


@router.get("/notifications")
async def get_notifications_list(unread_only: bool = False, channel: str = None, limit: int = 50):
    from backend.core.productivity import get_notifications
    return {"ok": True, "notifications": get_notifications().get_notifications(unread_only, channel, limit)}


@router.get("/notifications/summary")
async def get_notifications_summary():
    from backend.core.productivity import get_notifications
    return {"ok": True, "summary": get_notifications().get_summary()}


@router.put("/notifications/{notif_id}/read")
async def mark_notification_read(notif_id: int):
    from backend.core.productivity import get_notifications
    get_notifications().mark_read(notif_id)
    return {"ok": True}


@router.post("/notifications/read-all")
async def mark_all_notifications_read():
    from backend.core.productivity import get_notifications
    count = get_notifications().mark_all_read()
    return {"ok": True, "marked": count}


@router.post("/notifications/suppress")
async def suppress_notifications(body: dict):
    from backend.core.productivity import get_notifications
    get_notifications().suppress(body.get("enabled", True))
    return {"ok": True}


# ── Price Tracker ────────────────────────────────────────────────────

@router.post("/price-tracker/products")
async def add_tracked_product(body: dict):
    from backend.core.productivity import get_price_tracker
    result = get_price_tracker().add_product(
        name=body["name"],
        url=body["url"],
        store=body.get("store", ""),
        target_price=body.get("target_price"),
    )
    return {"ok": True, **result}


@router.get("/price-tracker/products")
async def list_tracked_products():
    from backend.core.productivity import get_price_tracker
    return {"ok": True, "products": get_price_tracker().list_products()}


@router.post("/price-tracker/products/{product_id}/price")
async def record_product_price(product_id: str, body: dict):
    from backend.core.productivity import get_price_tracker
    result = get_price_tracker().record_price(product_id, body["price"], body.get("currency", "USD"))
    return {"ok": True, **result}


@router.get("/price-tracker/products/{product_id}/history")
async def get_product_price_history(product_id: str, limit: int = 100):
    from backend.core.productivity import get_price_tracker
    return {"ok": True, "history": get_price_tracker().get_price_history(product_id, limit)}


@router.get("/price-tracker/alerts")
async def check_price_alerts():
    from backend.core.productivity import get_price_tracker
    return {"ok": True, "alerts": get_price_tracker().check_alerts()}


@router.delete("/price-tracker/products/{product_id}")
async def delete_tracked_product(product_id: str):
    from backend.core.productivity import get_price_tracker
    ok = get_price_tracker().delete_product(product_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return {"ok": True}


# ── System Optimizer ─────────────────────────────────────────────────

@router.get("/system/info")
async def get_system_info():
    from backend.core.productivity import get_system_optimizer
    return {"ok": True, "info": get_system_optimizer().get_system_info()}


@router.get("/system/disk")
async def get_disk_usage(path: str = "."):
    from backend.core.productivity import get_system_optimizer
    return {"ok": True, "usage": get_system_optimizer().get_disk_usage(path)}


@router.get("/system/large-files")
async def find_large_files(path: str = ".", min_mb: int = 100, limit: int = 20):
    from backend.core.productivity import get_system_optimizer
    return {"ok": True, "files": get_system_optimizer().find_large_files(path, min_mb, limit)}


# ═══════════════════════════════════════════════════════════════════════
#  Phase 18 — Offline + Sync + Auto-Update
# ═══════════════════════════════════════════════════════════════════════

# --- Offline / Connectivity ---

@router.get("/offline/status")
async def offline_status():
    from backend.core.offline_sync import get_offline_manager
    return {"ok": True, "status": get_offline_manager().get_status()}


@router.post("/offline/check")
async def offline_check():
    from backend.core.offline_sync import get_offline_manager
    return {"ok": True, "result": get_offline_manager().check_connectivity()}


@router.post("/offline/force")
async def offline_force(body: dict):
    from backend.core.offline_sync import get_offline_manager
    return {"ok": True, "result": get_offline_manager().force_state(body.get("state", "online"))}


@router.get("/offline/capable/{capability}")
async def offline_capable(capability: str):
    from backend.core.offline_sync import get_offline_manager
    return {"ok": True, "capable": get_offline_manager().is_capable(capability)}


# --- Sync ---

@router.get("/sync/status")
async def sync_status():
    from backend.core.offline_sync import get_sync_manager
    return {"ok": True, "status": get_sync_manager().get_status()}


@router.post("/sync/items")
async def sync_add_item(body: dict):
    from backend.core.offline_sync import get_sync_manager
    result = get_sync_manager().add_item(
        body["item_type"], body["content"], body.get("item_id"),
    )
    return {"ok": True, **result}


@router.get("/sync/pending")
async def sync_pending(limit: int = 100):
    from backend.core.offline_sync import get_sync_manager
    return {"ok": True, "items": get_sync_manager().get_pending(limit)}


@router.post("/sync/mark-synced")
async def sync_mark_synced(body: dict):
    from backend.core.offline_sync import get_sync_manager
    count = get_sync_manager().mark_synced(body.get("item_ids", []))
    return {"ok": True, "synced": count}


@router.post("/sync/receive")
async def sync_receive(body: dict):
    from backend.core.offline_sync import get_sync_manager
    result = get_sync_manager().receive_remote(body.get("items", []))
    return {"ok": True, **result}


@router.get("/sync/conflicts")
async def sync_conflicts(resolved: bool = False):
    from backend.core.offline_sync import get_sync_manager
    return {"ok": True, "conflicts": get_sync_manager().get_conflicts(resolved)}


@router.post("/sync/conflicts/{conflict_id}/resolve")
async def sync_resolve_conflict(conflict_id: str, body: dict):
    from backend.core.offline_sync import get_sync_manager
    ok = get_sync_manager().resolve_conflict(conflict_id, body.get("resolution", ""))
    return {"ok": ok}


# --- Skill Deprecation ---

@router.post("/skills/lifecycle/register")
async def skill_register(body: dict):
    from backend.core.offline_sync import get_skill_deprecation
    get_skill_deprecation().register_skill(body["skill_id"], body["skill_name"])
    return {"ok": True}


@router.post("/skills/lifecycle/use")
async def skill_record_use(body: dict):
    from backend.core.offline_sync import get_skill_deprecation
    get_skill_deprecation().record_use(body["skill_id"], body.get("success", True))
    return {"ok": True}


@router.post("/skills/lifecycle/evaluate")
async def skill_evaluate():
    from backend.core.offline_sync import get_skill_deprecation
    changes = get_skill_deprecation().evaluate_lifecycle()
    return {"ok": True, "changes": changes}


@router.get("/skills/lifecycle")
async def skill_list(status: str = None):
    from backend.core.offline_sync import get_skill_deprecation
    return {"ok": True, "skills": get_skill_deprecation().list_skills(status)}


@router.post("/skills/lifecycle/{skill_id}/reactivate")
async def skill_reactivate(skill_id: str):
    from backend.core.offline_sync import get_skill_deprecation
    return {"ok": get_skill_deprecation().reactivate(skill_id)}


@router.get("/skills/lifecycle/health")
async def skill_health():
    from backend.core.offline_sync import get_skill_deprecation
    return {"ok": True, "report": get_skill_deprecation().get_health_report()}


# --- Auto-Update ---

@router.get("/updater/status")
async def updater_status():
    from backend.core.auto_updater import get_auto_updater
    return {"ok": True, "status": get_auto_updater().get_status()}


@router.get("/updater/version")
async def updater_version():
    from backend.core.auto_updater import get_auto_updater
    return {"ok": True, "version": get_auto_updater().get_current_version()}


@router.post("/updater/check")
async def updater_check(body: dict = {}):
    from backend.core.auto_updater import get_auto_updater
    result = get_auto_updater().check_for_updates(body.get("url"))
    return {"ok": True, **result}


@router.get("/updater/channel")
async def updater_get_channel():
    from backend.core.auto_updater import get_auto_updater
    return {"ok": True, "channel": get_auto_updater().get_channel()}


@router.post("/updater/channel")
async def updater_set_channel(body: dict):
    from backend.core.auto_updater import get_auto_updater
    return {"ok": True, **get_auto_updater().set_channel(body["channel"])}


@router.post("/updater/record")
async def updater_record(body: dict):
    from backend.core.auto_updater import get_auto_updater
    result = get_auto_updater().record_update(
        body["from_version"], body["to_version"],
        body.get("status", "success"), body.get("notes"),
    )
    return {"ok": True, **result}


@router.get("/updater/history")
async def updater_history(limit: int = 20):
    from backend.core.auto_updater import get_auto_updater
    return {"ok": True, "history": get_auto_updater().get_update_history(limit)}


@router.get("/updater/rollback-info")
async def updater_rollback_info():
    from backend.core.auto_updater import get_auto_updater
    return {"ok": True, **get_auto_updater().rollback_info()}


# ── Crews Multi-Agente (Phase 2) ──────────────────────────────────


@router.get("/crews")
async def list_crews():
    """Lista todas las definiciones de crews."""
    from backend.core.crew_engine import get_crew_engine
    return {"crews": get_crew_engine().list_crews()}


@router.get("/crews/runs")
async def list_crew_runs():
    """Lista todas las ejecuciones de crews."""
    from backend.core.crew_engine import get_crew_engine
    return {"runs": get_crew_engine().list_runs()}


@router.get("/crews/{crew_id}")
async def get_crew(crew_id: str):
    """Obtiene una crew por ID."""
    from backend.core.crew_engine import get_crew_engine
    defn = get_crew_engine().get_crew(crew_id)
    if defn is None:
        raise HTTPException(status_code=404, detail=f"Crew not found: {crew_id}")
    return defn.to_dict()


@router.post("/crews")
async def create_crew(request: Request):
    """Crea una nueva crew."""
    from backend.core.crew_engine import get_crew_engine
    body = await request.json()
    defn = get_crew_engine().create_crew(body)
    return {"ok": True, "crew": defn.to_dict()}


@router.put("/crews/{crew_id}")
async def update_crew(crew_id: str, request: Request):
    """Actualiza una crew existente."""
    from backend.core.crew_engine import get_crew_engine
    body = await request.json()
    defn = get_crew_engine().update_crew(crew_id, body)
    if defn is None:
        raise HTTPException(status_code=404, detail=f"Crew not found: {crew_id}")
    return {"ok": True, "crew": defn.to_dict()}


@router.delete("/crews/{crew_id}")
async def delete_crew(crew_id: str):
    """Elimina una crew."""
    from backend.core.crew_engine import get_crew_engine
    ok = get_crew_engine().delete_crew(crew_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Crew not found: {crew_id}")
    return {"ok": True, "deleted": crew_id}


@router.post("/crews/{crew_id}/run")
async def run_crew(crew_id: str, request: Request):
    """Lanza una ejecución de crew con las tareas indicadas."""
    from backend.api.websocket_handler import _agent_core, sio
    from backend.core.crew_engine import get_crew_engine

    if _agent_core is None:
        raise HTTPException(status_code=500, detail="AgentCore no inicializado")

    body = await request.json()
    tasks = body.get("tasks", [])
    if not tasks:
        raise HTTPException(status_code=400, detail="Se requiere al menos una tarea")

    engine = get_crew_engine()

    async def on_crew_update(event: dict):
        await sio.emit("crew:update", event)

    run = await engine.run_crew(
        crew_id=crew_id,
        tasks=tasks,
        router=_agent_core.router,
        subagent_orchestrator=_agent_core.sub_orchestrator,
        session_id=body.get("session_id", "crew"),
        parent_mode_key=body.get("mode", "normal"),
        parent_task_limit_usd=body.get("budget_usd", 0.0),
        planner=getattr(_agent_core, "planner", None),
        on_update=on_crew_update,
    )

    await sio.emit("crew:finished", run.to_dict())
    return {"ok": True, "run": run.to_dict()}
