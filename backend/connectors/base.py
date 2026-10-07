"""
G-Mini Agent — Base de conectores.

Un conector expone acciones (`ConnectorAction`) que el agente invoca con
[ACTION:connector(id="...", action="...", params={...})] y que la UI lista en
Ajustes > Conectores. Los secretos van al keyring (vault `connector_<id>_<campo>`)
y el resto de ajustes a config.yaml (connectors.<id>.<campo>).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import aiohttp
from loguru import logger

from backend.config import config

USER_AGENT = "G-Mini-Agent/0.2 (+https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent)"
DEFAULT_TIMEOUT = 20.0
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class ConnectorError(RuntimeError):
    """Error controlado de un conector (se muestra tal cual al usuario/agente)."""


async def read_body(resp: aiohttp.ClientResponse, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    """Lee la respuesta completa con tope. `content.read(n)` devuelve solo lo que haya
    en el buffer en ese momento: con respuestas grandes cortaba el JSON por la mitad."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > limit:
            raise ConnectorError("La respuesta es demasiado grande.")
        chunks.append(chunk)
    return b"".join(chunks)


@dataclass
class ConnectorField:
    key: str
    label: str
    secret: bool = False
    required: bool = False
    placeholder: str = ""
    help: str = ""
    kind: str = "text"            # text | password | url | path | bool | textarea
    default: Any = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "secret": self.secret,
            "required": self.required,
            "placeholder": self.placeholder,
            "help": self.help,
            "kind": "password" if self.secret else self.kind,
            "default": self.default,
        }


@dataclass
class ConnectorAction:
    name: str
    description: str
    handler: Callable[..., Awaitable[Any]]
    params: dict[str, dict[str, Any]] = field(default_factory=dict)
    writes: bool = False          # modifica datos externos (se pide confirmación según autonomía)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "params": self.params,
            "writes": self.writes,
        }


def _is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


async def ensure_public_url(url: str) -> str:
    """Evita SSRF: solo http(s) hacia IPs públicas."""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConnectorError("URL inválida: usa http:// o https://.")
    host = parsed.hostname
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ConnectorError(f"No se pudo resolver {host}.") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses or not all(_is_public_ip(addr.split("%")[0]) for addr in addresses):
        raise ConnectorError("Por seguridad no se permiten direcciones locales o privadas.")
    return parsed.geturl()


class Connector:
    """Clase base. Las subclases definen metadatos, fields y actions()."""

    id: str = "base"
    label: str = "Conector"
    description: str = ""
    category: str = "general"     # productividad | informacion | desarrollo | finanzas | notas
    icon: str = "plug"            # nombre de icono Lucide
    docs_url: str = ""
    fields: list[ConnectorField] = []
    requires_setup: bool = False  # True si no funciona sin configurar campos requeridos

    # ── Ajustes y secretos ────────────────────────────────────────────
    def vault_name(self, key: str) -> str:
        return f"connector_{self.id}_{key}"

    def secret(self, key: str) -> str:
        return (config.get_api_key(self.vault_name(key)) or "").strip()

    def setting(self, key: str, default: Any = None) -> Any:
        fallback = default
        if fallback is None:
            for f in self.fields:
                if f.key == key:
                    fallback = f.default
                    break
        return config.get("connectors", self.id, key, default=fallback)

    def value(self, key: str) -> Any:
        for f in self.fields:
            if f.key == key:
                return self.secret(key) if f.secret else self.setting(key)
        return self.setting(key)

    def is_enabled(self) -> bool:
        return bool(config.get("connectors", self.id, "enabled", default=True))

    def is_configured(self) -> bool:
        for f in self.fields:
            if f.required and not self.value(f.key):
                return False
        return True

    def status(self) -> dict[str, Any]:
        fields = []
        for f in self.fields:
            data = f.to_dict()
            if f.secret:
                data["configured"] = bool(self.secret(f.key))
            else:
                data["value"] = self.setting(f.key)
            fields.append(data)
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "category": self.category,
            "icon": self.icon,
            "docs_url": self.docs_url,
            "enabled": self.is_enabled(),
            "configured": self.is_configured(),
            "requires_setup": self.requires_setup,
            "fields": fields,
            "actions": [a.to_dict() for a in self.actions()],
        }

    # ── Acciones ──────────────────────────────────────────────────────
    def actions(self) -> list[ConnectorAction]:
        return []

    def get_action(self, name: str) -> ConnectorAction | None:
        for action in self.actions():
            if action.name == name:
                return action
        return None

    async def test(self) -> dict[str, Any]:
        """Verificación rápida (sobrescribible)."""
        if not self.is_configured():
            return {"ok": False, "message": "Faltan datos obligatorios."}
        return {"ok": True, "message": "Listo."}

    # ── HTTP ──────────────────────────────────────────────────────────
    async def http_request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
        expect: str = "json",
    ) -> Any:
        merged = {"User-Agent": USER_AGENT, "Accept": "application/json" if expect == "json" else "*/*"}
        merged.update(headers or {})
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.request(method, url, params=params, headers=merged, json=json_body) as resp:
                    raw = await read_body(resp)
                    if resp.status >= 400:
                        detail = raw[:300].decode("utf-8", errors="replace")
                        raise ConnectorError(f"{self.label}: HTTP {resp.status} — {detail}")
                    if expect == "json":
                        import json

                        return json.loads(raw.decode("utf-8", errors="replace") or "null")
                    if expect == "bytes":
                        return raw
                    charset = resp.charset or "utf-8"
                    return raw.decode(charset, errors="replace")
        except ConnectorError:
            raise
        except asyncio.TimeoutError as exc:
            raise ConnectorError(f"{self.label}: tiempo de espera agotado.") from exc
        except aiohttp.ClientError as exc:
            raise ConnectorError(f"{self.label}: error de red ({exc}).") from exc

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        return await self.http_request("GET", url, **kwargs)


def coerce_params(action: ConnectorAction, params: dict[str, Any] | None) -> dict[str, Any]:
    """Valida y convierte parámetros según la declaración de la acción."""
    params = dict(params or {})
    out: dict[str, Any] = {}
    for name, spec in action.params.items():
        if name in params and params[name] not in (None, ""):
            value = params[name]
            kind = spec.get("type", "string")
            try:
                if kind == "integer":
                    value = int(value)
                elif kind == "number":
                    value = float(value)
                elif kind == "boolean":
                    value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "si", "sí", "yes")
                else:
                    value = str(value)
            except (TypeError, ValueError) as exc:
                raise ConnectorError(f"Parámetro '{name}' inválido: se esperaba {kind}.") from exc
            if "minimum" in spec and isinstance(value, (int, float)):
                value = max(spec["minimum"], value)
            if "maximum" in spec and isinstance(value, (int, float)):
                value = min(spec["maximum"], value)
            out[name] = value
        elif spec.get("required"):
            raise ConnectorError(f"Falta el parámetro obligatorio '{name}'.")
        elif "default" in spec:
            out[name] = spec["default"]
    unknown = set(params) - set(action.params)
    if unknown:
        logger.debug(f"connector {action.name}: parámetros ignorados {sorted(unknown)}")
    return out
