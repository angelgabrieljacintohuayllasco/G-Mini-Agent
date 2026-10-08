"""
G-Mini Agent - Entry point del backend.
Levanta FastAPI + Socket.IO montado en ASGI.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Agregar el directorio raiz al path para imports
CODE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE_ROOT))

# Antes de cualquier import que pueda traer winrt y su msvcp140.dll vieja
# (Whisper se caía al crear el modelo; ver backend/utils/msvc_runtime.py).
from backend.utils.msvc_runtime import preload_msvc_runtime  # noqa: E402

preload_msvc_runtime()

import socketio
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request as StarletteRequest
from loguru import logger

from backend.api.routes import router as api_router
from backend.api.v1 import ApiError, api_error_handler
from backend.api.v1 import router as api_v1_router
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
    # Curator de skills del agente: revisa cada hora, actúa una vez al día con el agente inactivo.
    {"name": "skill_curator", "task_type": "skill_curator",
     "trigger_type": "interval", "interval_seconds": 3600},
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

    # Tareas del trabajador 24/7 que quedaron a medias si el servicio se reinició.
    try:
        from backend.core import remote_tasks

        await remote_tasks.recover_after_restart()
    except Exception as exc:
        logger.warning(f"No se pudieron retomar las tareas remotas: {exc}")

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
    app.include_router(api_v1_router, prefix="/api")
    app.add_exception_handler(ApiError, api_error_handler)

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


def _parse_args(argv: list[str] | None = None):
    import argparse

    parser = argparse.ArgumentParser(prog="gmini-backend", description="Núcleo de G-Mini Agent")
    parser.add_argument("--headless", action="store_true",
                        help="Modo servidor: sin visión ni control de escritorio (VPS, Raspberry Pi, Docker)")
    parser.add_argument("--host", help="Interfaz de escucha (127.0.0.1 por defecto; 0.0.0.0 para la red)")
    parser.add_argument("--port", type=int, help="Puerto (8765 por defecto)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    """Entry point."""
    args = _parse_args(argv)
    from backend.config import CODE_DIR, ROOT_DIR

    if ROOT_DIR != CODE_DIR:
        os.chdir(ROOT_DIR)  # las rutas relativas de la config ("data/...") van a GMINI_HOME
        logger.info(f"Datos en GMINI_HOME: {ROOT_DIR}")
    if args.headless:
        os.environ["GMINI_HEADLESS"] = "1"
    host = args.host or os.environ.get("GMINI_BIND_HOST") or config.get("server", "host", default="127.0.0.1")
    port = args.port or config.get("server", "port", default=8765)
    os.environ["GMINI_BIND_HOST"] = str(host)  # local_auth decide la validación de Host según el bind
    os.environ["GMINI_BIND_PORT"] = str(port)

    if str(host) not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(f"Escuchando en {host}: todas las rutas exigen token (usa Tailscale/VPN o TLS para exponerlo).")
    logger.info(f"Iniciando servidor en {host}:{port}" + (" (headless)" if args.headless else ""))
    app = create_app()

    from backend.security.local_auth import extra_hosts

    extras = [h for h in extra_hosts() if h != str(host)] if not args.host else []
    if extras:
        # 127.0.0.1 para la app y además las IPs de server.extra_hosts (p. ej. Tailscale),
        # sin abrir el núcleo a toda la red como haría 0.0.0.0.
        server = uvicorn.Server(uvicorn.Config(app, log_level="info", access_log=False))
        server.run(sockets=_listen_sockets([str(host), *extras], int(port)))
        return
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )


def _listen_sockets(hosts: list[str], port: int) -> list:
    import socket

    sockets = []
    for bind in hosts:
        family = socket.AF_INET6 if ":" in bind else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        if os.name != "nt":  # en Windows SO_REUSEADDR deja a otro proceso robar el puerto
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind, port))
            sock.listen(2048)
        except OSError as exc:
            sock.close()
            if bind in ("127.0.0.1", "localhost", "::1"):
                raise
            logger.warning(f"No pude escuchar en {bind}:{port} ({exc}); sigo solo en el resto")
            continue
        sock.set_inheritable(True)
        logger.info(f"Escuchando también en {bind}:{port}" if sockets else f"Escuchando en {bind}:{port}")
        sockets.append(sock)
    return sockets


if __name__ == "__main__":
    main()
