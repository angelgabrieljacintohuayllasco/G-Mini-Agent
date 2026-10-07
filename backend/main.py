"""
G-Mini Agent - Entry point del backend.
Levanta FastAPI + Socket.IO montado en ASGI.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Agregar el directorio raiz al path para imports
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import socketio
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request as StarletteRequest
from loguru import logger

from backend.api.routes import router as api_router
from backend.api.websocket_handler import sio, set_agent_core
from backend.automation.editor_bridge import get_editor_bridge
from backend.automation.extension_bridge import get_bridge
from backend.config import config
from backend.core.agent import AgentCore
from backend.core.gateway_service import get_gateway
from backend.core.scheduler import get_scheduler
from backend.security import local_auth
from backend.utils.logger import logger  # noqa: F811 - configura loguru

# Jobs internos que se crean una vez si faltan (el usuario puede pausarlos).
BUILTIN_JOBS: tuple[dict, ...] = (
    # Reporte semanal de presupuesto: lunes 9:00 UTC.
    {"name": "budget_weekly_report", "task_type": "budget_weekly_report",
     "trigger_type": "cron", "cron_expression": "0 9 * * 1"},
    # Consolidación de memoria: solo actúa si el agente lleva un rato inactivo.
    {"name": "learning_consolidate", "task_type": "learning_consolidate",
     "trigger_type": "interval", "interval_seconds": 900},
)


async def _ensure_builtin_jobs(scheduler) -> None:
    try:
        existing = {job.get("name") for job in await scheduler.list_jobs()}
    except Exception as exc:
        logger.warning(f"No se pudieron listar los jobs: {exc}")
        return
    for spec in BUILTIN_JOBS:
        if spec["name"] in existing:
            continue
        try:
            await scheduler.create_job(payload={}, enabled=True, **spec)
            logger.info(f"Job interno creado: {spec['name']}")
        except Exception as exc:
            logger.warning(f"No se pudo crear el job {spec['name']}: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("=" * 60)
    logger.info("  G-Mini Agent - Backend Starting")
    logger.info(f"  Version: {config.get('app', 'version', default='0.1.0')}")
    logger.info("=" * 60)

    # Quien ya usaba G-Mini no debe ver el asistente inicial (lee keyring y SQLite: en un hilo).
    try:
        from backend.core.onboarding import migrate_existing_install

        await asyncio.to_thread(migrate_existing_install)
    except Exception as exc:
        logger.warning(f"No se pudo revisar el estado del asistente inicial: {exc}")

    agent_core = AgentCore()
    await agent_core.initialize()
    set_agent_core(agent_core)
    logger.info("AgentCore inicializado y conectado al WebSocket handler")

    gateway = get_gateway()
    gateway.attach_socket_server(sio)
    gateway.attach_agent_core(agent_core)
    await gateway.initialize()
    logger.info("GatewayService inicializado")

    scheduler = get_scheduler()
    await scheduler.initialize()
    logger.info("SchedulerService inicializado")

    await _ensure_builtin_jobs(scheduler)

    try:
        yield
    finally:
        await scheduler.shutdown()
        await gateway.shutdown()
        # Cerrar sesiones MCP persistentes (runtime compartido + el del planner si es propio)
        try:
            from backend.core.mcp_runtime import shutdown_mcp_runtime

            if agent_core._planner and hasattr(agent_core._planner, '_mcp_runtime'):
                agent_core._planner._mcp_runtime.shutdown()
            shutdown_mcp_runtime()
            logger.info("MCPSessionPool cerrado correctamente")
        except Exception as exc:
            logger.warning(f"Error cerrando MCPSessionPool: {exc}")
        try:
            await agent_core.shutdown()
        except Exception as exc:
            logger.warning(f"No se pudo cerrar AgentCore limpiamente: {exc}")
        logger.info("G-Mini Agent - Backend Shutting Down")


def create_app() -> socketio.ASGIApp:
    """Crea y configura la aplicacion FastAPI + Socket.IO."""
    app = FastAPI(
        title="G-Mini Agent",
        version=config.get("app", "version", default="0.1.0"),
        docs_url="/docs",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.get("server", "cors_origins", default=["http://127.0.0.1:8765", "http://localhost:8765"]),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    local_auth.get_session_token()  # crea data/runtime/session_token al arrancar

    @app.middleware("http")
    async def local_auth_middleware(request: StarletteRequest, call_next):
        if not local_auth.host_is_allowed(request.headers.get("host")):
            return JSONResponse(
                status_code=403,
                content={"error": {"code": "invalid_host", "message": "Host no permitido"}},
            )
        path = request.url.path
        if (
            path.startswith("/api/")
            and request.method != "OPTIONS"
            and local_auth.auth_required()
            and not local_auth.is_public_route(request.method, path)
        ):
            token = local_auth.extract_token(request.headers, request.query_params)
            info = local_auth.verify_token(token)
            if info is None:
                return JSONResponse(
                    status_code=401,
                    content={"error": {"code": "invalid_token", "message": "Token ausente o inválido"}},
                )
            request.state.auth = info
        return await call_next(request)

    app.include_router(api_router, prefix="/api")

    ext_bridge = get_bridge()
    editor_bridge = get_editor_bridge()

    async def _reject_untrusted_ws(ws: WebSocket, *, allow_extensions: bool) -> bool:
        """Cierra la conexión si viene de una web ajena o de un Host no permitido."""
        origin = ws.headers.get("origin")
        if not local_auth.host_is_allowed(ws.headers.get("host")) or local_auth.browser_origin_is_untrusted(
            origin, allow_extensions=allow_extensions
        ):
            logger.warning(f"WebSocket {ws.url.path} rechazado (origin={origin!r})")
            await ws.close(code=1008)
            return True
        return False

    @app.websocket("/ws/extension")
    async def ws_extension(ws: WebSocket):
        if await _reject_untrusted_ws(ws, allow_extensions=True):
            return
        await ext_bridge.handle_websocket(ws)

    @app.websocket("/ws/editor")
    async def ws_editor(ws: WebSocket):
        if await _reject_untrusted_ws(ws, allow_extensions=False):
            return
        await editor_bridge.handle_websocket(ws)

    return socketio.ASGIApp(sio, other_asgi_app=app)


def main():
    """Entry point."""
    host = config.get("server", "host", default="127.0.0.1")
    port = config.get("server", "port", default=8765)

    logger.info(f"Iniciando servidor en {host}:{port}")
    app = create_app()

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
