"""Otros G-Mini (un VPS, una Raspberry Pi, otra PC) a los que este puede delegar tareas.

Se emparejan con el código de 6 dígitos que muestra el otro equipo
(POST /api/v1/pairing/claim). El token de dispositivo queda en el vault como
remote_server_<id>; en data/runtime/remote_servers.json solo van la URL y el
nombre. Las tareas usan la API remota v1 (docs/protocol/remote-api-v1.md).
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import secrets
import socket
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import aiohttp

from backend.config import ROOT_DIR, config

STORE: Path = ROOT_DIR / "data" / "runtime" / "remote_servers.json"
_TIMEOUT = aiohttp.ClientTimeout(total=20)
_FINISHED = {"done", "failed", "cancelled"}
DEFAULT_PORT = 8765


class RemoteServerError(Exception):
    """Error con un mensaje listo para mostrar al usuario."""


@dataclass
class RemoteServer:
    id: str
    name: str
    url: str
    device_id: str = ""
    agent_name: str = ""
    added_at: float = 0.0


def _vault_name(server_id: str) -> str:
    return f"remote_server_{server_id}"


def normalize_url(url: str) -> str:
    """'100.71.131.70' -> 'http://100.71.131.70:8765'.

    Con esquema escrito (https://gmini.midominio.com detrás de un proxy) se
    respeta tal cual; el puerto 8765 solo se agrega a una dirección pelada.
    """
    url = str(url or "").strip().rstrip("/")
    if not url:
        raise RemoteServerError("Falta la dirección del otro G-Mini")
    if re.match(r"^https?://", url, re.IGNORECASE):
        return url
    host, _, path = url.partition("/")
    if not re.search(r":\d+$", host) and not host.endswith("]"):
        host = f"{host}:{DEFAULT_PORT}"
    return f"http://{host}" + (f"/{path}" if path else "")


def _load() -> list[RemoteServer]:
    try:
        raw = json.loads(STORE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return [RemoteServer(**item) for item in raw if isinstance(item, dict) and item.get("id") and item.get("url")]


def _save(servers: list[RemoteServer]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".remote-", dir=str(STORE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump([asdict(s) for s in servers], handle, indent=2, ensure_ascii=False)
        os.replace(tmp, STORE)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def list_servers() -> list[dict[str, Any]]:
    return [asdict(s) for s in _load()]


def find(ref: str = "") -> RemoteServer:
    servers = _load()
    if not servers:
        raise RemoteServerError("No hay otros G-Mini emparejados. Empareja uno en Ajustes > Dispositivos.")
    ref = str(ref or "").strip().lower()
    if not ref:
        if len(servers) == 1:
            return servers[0]
        raise RemoteServerError("Hay varios G-Mini emparejados; indica cuál: " + ", ".join(s.name for s in servers))
    for server in servers:
        if ref in (server.id.lower(), server.name.lower(), server.url.lower()):
            return server
    raise RemoteServerError(f"No conozco un G-Mini llamado '{ref}'. Emparejados: " + ", ".join(s.name for s in servers))


def _error_text(body: Any, status: int) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if body.get("detail"):
            return str(body["detail"])
    return f"HTTP {status}"


async def _call(method: str, url: str, *, token: str = "", payload: dict | None = None, label: str = "") -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.request(method, url, json=payload, headers=headers) as resp:
                try:
                    body = await resp.json(content_type=None)
                except Exception:
                    body = None
                if resp.status >= 400:
                    raise RemoteServerError(f"{label or url}: {_error_text(body, resp.status)}")
                return body
    except RemoteServerError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        raise RemoteServerError(f"No pude conectar con {label or url}: {exc or 'tiempo agotado'}") from exc


async def pair(url: str, code: str, name: str = "") -> dict[str, Any]:
    """Canjea el código de emparejamiento del otro equipo y guarda el servidor."""
    url = normalize_url(url)
    code = re.sub(r"\D", "", str(code or ""))
    if len(code) != 6:
        raise RemoteServerError("El código de emparejamiento tiene 6 dígitos")
    body = await _call("POST", f"{url}/api/v1/pairing/claim", label=url, payload={
        "code": code,
        "device_name": socket.gethostname() or "G-Mini",
        "device_type": "desktop",
        "platform": platform.system().lower(),
    })
    token = str((body or {}).get("token") or "")
    if not token:
        raise RemoteServerError("El otro G-Mini no devolvió un token")

    server = RemoteServer(
        id="srv_" + secrets.token_hex(4),
        name=str(name or (body or {}).get("server_name") or url).strip()[:60],
        url=url,
        device_id=str((body or {}).get("device_id") or ""),
        agent_name=str((body or {}).get("agent_name") or ""),
        added_at=time.time(),
    )
    config.set_api_key(_vault_name(server.id), token)
    servers = []
    for existing in _load():
        if existing.url == url:  # volver a emparejar reemplaza al anterior
            config.delete_api_key(_vault_name(existing.id))
        else:
            servers.append(existing)
    servers.append(server)
    _save(servers)
    return asdict(server)


def remove(ref: str) -> dict[str, Any]:
    server = find(ref)
    config.delete_api_key(_vault_name(server.id))
    _save([s for s in _load() if s.id != server.id])
    return asdict(server)


async def _request(server: RemoteServer, method: str, path: str, payload: dict | None = None) -> Any:
    token = config.get_api_key(_vault_name(server.id))
    if not token:
        raise RemoteServerError(f"Falta el token de {server.name}; vuelve a emparejarlo")
    return await _call(method, server.url + path, token=token, payload=payload, label=server.name)


async def status(ref: str = "") -> dict[str, Any]:
    server = find(ref)
    health = await _call("GET", server.url + "/api/v1/health", label=server.name)
    me = await _request(server, "GET", "/api/v1/me")
    return {"server": server.name, "url": server.url, "health": health, "me": me}


async def delegate(ref: str, prompt: str, *, title: str = "", wait: bool = False,
                   timeout_s: float = 300.0, poll_s: float = 3.0) -> dict[str, Any]:
    """Encola una tarea en el otro G-Mini; con wait espera el resultado hasta timeout_s."""
    prompt = str(prompt or "").strip()
    if not prompt:
        raise RemoteServerError("Falta la tarea a delegar")
    server = find(ref)
    created = await _request(server, "POST", "/api/v1/tasks", {"prompt": prompt, "title": title or prompt[:60]})
    task_id = str((created or {}).get("task_id") or "")
    if not wait or not task_id:
        return {"server": server.name, **(created or {})}
    deadline = time.monotonic() + max(5.0, timeout_s)
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_s)
        task = await _request(server, "GET", f"/api/v1/tasks/{task_id}")
        if str((task or {}).get("status")) in _FINISHED:
            return {"server": server.name, **task}
    return {"server": server.name, "task_id": task_id, "status": "running",
            "note": "Sigue corriendo; consulta luego con remote_task_status."}


async def task_status(ref: str, task_id: str) -> dict[str, Any]:
    server = find(ref)
    task = await _request(server, "GET", f"/api/v1/tasks/{str(task_id).strip()}")
    return {"server": server.name, **(task or {})}


def build_prompt_index() -> str:
    servers = _load()
    if not servers:
        return ""
    lines = ["[OTROS G-MINI EMPAREJADOS] Puedes delegarles tareas largas o que deben seguir cuando esta PC se apague:"]
    for server in servers:
        lines.append(f"- {server.name} ({server.url})")
    lines.append(
        'Usa remote_delegate(server="<nombre>", task="<qué hacer, completo>", wait=true) para esperar el '
        "resultado, o wait=false y luego remote_task_status(server=..., task_id=...)."
    )
    return "\n".join(lines)
