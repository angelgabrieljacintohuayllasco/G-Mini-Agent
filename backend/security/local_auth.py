"""
G-Mini Agent — Autenticación del núcleo.

Todo cliente del núcleo (UI de escritorio, CLI, dispositivos emparejados,
scripts) presenta un token:

- `session`: se genera al arrancar (o lo entrega Electron por la variable
  GMINI_SESSION_TOKEN) y se guarda en data/runtime/session_token para que la
  CLI de la misma máquina pueda leerlo.
- `device` / `api`: emitidos al emparejar o desde Ajustes; se guardan como
  hash SHA-256 en data/runtime/devices.json.

Además, cuando el núcleo escucha en loopback, solo acepta peticiones cuyo
Host sea 127.0.0.1/localhost con el puerto configurado. Eso bloquea el DNS
rebinding (una web maliciosa resolviendo su dominio a 127.0.0.1) y, junto con
el token obligatorio, el CSRF desde páginas abiertas en el navegador.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from backend.config import ROOT_DIR, config

RUNTIME_DIR = ROOT_DIR / "data" / "runtime"
SESSION_TOKEN_FILE = RUNTIME_DIR / "session_token"
DEVICES_FILE = RUNTIME_DIR / "devices.json"

ALL_SCOPES = ("chat", "voice", "tasks", "node", "admin")
DEFAULT_DEVICE_SCOPES = ("chat", "voice", "tasks", "node")
TOKEN_HEADER = "x-gmini-token"

PAIRING_CODE_TTL_SECONDS = 300
PAIRING_MAX_FAILURES = 5

# Rutas que no exigen token (siguen pasando por la validación de Host).
PUBLIC_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/health"),
    ("GET", "/api/v1/health"),
    ("POST", "/api/v1/pairing/claim"),
)
# Prefijos públicos: medios generados (se muestran en <img>/<video>, que no
# pueden enviar cabeceras) y webhooks externos, que traen su propio secreto.
PUBLIC_PREFIXES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/media/"),
    ("POST", "/api/scheduler/webhooks/"),
    ("POST", "/api/webhooks/incoming/"),
)

_lock = threading.Lock()
_session_token: str | None = None


@dataclass
class AuthInfo:
    kind: str                      # session | device | api
    scopes: tuple[str, ...]
    device_id: str = ""
    device_name: str = ""

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


@dataclass
class DeviceRecord:
    id: str
    name: str
    kind: str = "device"           # device | api
    device_type: str = "custom"
    platform: str = ""
    scopes: list[str] = field(default_factory=lambda: list(DEFAULT_DEVICE_SCOPES))
    token_sha256: str = ""
    created_at: float = field(default_factory=time.time)
    last_seen_at: float = 0.0

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("token_sha256", None)
        return data


# ── Utilidades ───────────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _server_port() -> int:
    # GMINI_BIND_PORT lo fija main.py con el puerto real (--port gana a la config).
    try:
        return int(os.environ.get("GMINI_BIND_PORT") or config.get("server", "port", default=8765) or 8765)
    except (TypeError, ValueError):
        return 8765


def _bind_host() -> str:
    return str(os.environ.get("GMINI_BIND_HOST") or config.get("server", "host", default="127.0.0.1") or "127.0.0.1")


def is_loopback_bind() -> bool:
    return _bind_host() in ("127.0.0.1", "localhost", "::1")


def auth_required() -> bool:
    """El token es obligatorio salvo que se apague explícitamente (solo desarrollo)."""
    return bool(config.get("server", "require_token", default=True))


# ── Token de sesión ──────────────────────────────────────────────────────

def get_session_token() -> str:
    """Token de sesión del proceso; lo crea (o adopta el de Electron) la primera vez."""
    global _session_token
    with _lock:
        if _session_token:
            return _session_token
        env_token = str(os.environ.get("GMINI_SESSION_TOKEN") or "").strip()
        token = env_token if len(env_token) >= 32 else secrets.token_urlsafe(32)
        try:
            _write_private(SESSION_TOKEN_FILE, token)
        except OSError as exc:
            logger.warning(f"local_auth: no se pudo escribir el token de sesión: {exc}")
        _session_token = token
        return token


def reset_session_token_for_tests(token: str | None = None) -> None:
    global _session_token
    with _lock:
        _session_token = token


# ── Dispositivos y tokens de API ─────────────────────────────────────────

def _load_devices() -> list[DeviceRecord]:
    try:
        raw = json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
        return [DeviceRecord(**item) for item in raw.get("devices", []) if isinstance(item, dict)]
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.error(f"local_auth: devices.json ilegible ({exc}); no se aceptan tokens de dispositivo")
        return []


def _save_devices(devices: list[DeviceRecord]) -> None:
    _write_private(DEVICES_FILE, json.dumps({"devices": [asdict(d) for d in devices]}, indent=2))


def issue_device_token(
    name: str,
    *,
    kind: str = "device",
    device_type: str = "custom",
    platform: str = "",
    scopes: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, DeviceRecord]:
    """Crea un token nuevo. Devuelve el token en claro (solo esta vez) y el registro."""
    clean_scopes = [s for s in (scopes or DEFAULT_DEVICE_SCOPES) if s in ALL_SCOPES]
    prefix = "gm_api_" if kind == "api" else "gm_dev_"
    token = prefix + secrets.token_urlsafe(32)
    record = DeviceRecord(
        id=("api_" if kind == "api" else "dev_") + secrets.token_hex(6),
        name=str(name or "Dispositivo")[:80],
        kind=kind,
        device_type=str(device_type or "custom")[:32],
        platform=str(platform or "")[:64],
        scopes=clean_scopes,
        token_sha256=_hash_token(token),
    )
    with _lock:
        devices = _load_devices()
        devices.append(record)
        _save_devices(devices)
    return token, record


def list_devices() -> list[dict[str, Any]]:
    with _lock:
        return [d.public_dict() for d in _load_devices()]


def revoke_device(device_id: str) -> bool:
    with _lock:
        devices = _load_devices()
        kept = [d for d in devices if d.id != device_id]
        if len(kept) == len(devices):
            return False
        _save_devices(kept)
        return True


def verify_token(token: str | None) -> AuthInfo | None:
    token = str(token or "").strip()
    if not token:
        return None
    if hmac.compare_digest(token, get_session_token()):
        return AuthInfo(kind="session", scopes=ALL_SCOPES)
    digest = _hash_token(token)
    with _lock:
        devices = _load_devices()
        for device in devices:
            if device.token_sha256 and hmac.compare_digest(device.token_sha256, digest):
                now = time.time()
                if now - device.last_seen_at > 60:
                    device.last_seen_at = now
                    try:
                        _save_devices(devices)
                    except OSError:
                        pass
                return AuthInfo(
                    kind=device.kind,
                    scopes=tuple(device.scopes),
                    device_id=device.id,
                    device_name=device.name,
                )
    return None


# ── Emparejamiento ───────────────────────────────────────────────────────

@dataclass
class _PairingCode:
    code: str
    label: str
    device_type: str
    scopes: list[str]
    expires_at: float
    failures: int = 0


_pairing_codes: dict[str, _PairingCode] = {}
_claim_attempts: dict[str, list[float]] = {}


def create_pairing_code(label: str = "", device_type: str = "custom", scopes: list[str] | None = None) -> dict[str, Any]:
    code = f"{secrets.randbelow(1_000_000):06d}"
    entry = _PairingCode(
        code=code,
        label=str(label or "")[:80],
        device_type=str(device_type or "custom")[:32],
        scopes=[s for s in (scopes or DEFAULT_DEVICE_SCOPES) if s in ALL_SCOPES and s != "admin"],
        expires_at=time.time() + PAIRING_CODE_TTL_SECONDS,
    )
    with _lock:
        now = time.time()
        for key in [k for k, v in _pairing_codes.items() if v.expires_at < now]:
            _pairing_codes.pop(key, None)
        _pairing_codes[code] = entry
    return {"code": code, "expires_at": entry.expires_at}


def _rate_limited(client_ip: str, limit: int = 5, window: float = 60.0) -> bool:
    now = time.time()
    attempts = [t for t in _claim_attempts.get(client_ip, []) if now - t < window]
    attempts.append(now)
    _claim_attempts[client_ip] = attempts
    return len(attempts) > limit


def claim_pairing_code(
    code: str,
    *,
    device_name: str,
    device_type: str = "",
    platform: str = "",
    client_ip: str = "",
) -> tuple[str, DeviceRecord]:
    """Canjea un código. Lanza PermissionError si no es válido o hay demasiados intentos."""
    with _lock:
        if _rate_limited(client_ip or "unknown"):
            raise PermissionError("rate_limited")
        entry = _pairing_codes.get(str(code or "").strip())
        if not entry or entry.expires_at < time.time():
            for pending in _pairing_codes.values():
                pending.failures += 1
            for key in [k for k, v in _pairing_codes.items() if v.failures >= PAIRING_MAX_FAILURES]:
                _pairing_codes.pop(key, None)
            raise PermissionError("invalid_code")
        _pairing_codes.pop(entry.code, None)
    return issue_device_token(
        device_name or entry.label or "Dispositivo",
        kind="device",
        device_type=device_type or entry.device_type,
        platform=platform,
        scopes=entry.scopes,
    )


# ── Validación de peticiones ─────────────────────────────────────────────

def allowed_hosts() -> set[str]:
    port = _server_port()
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    extra = config.get("server", "allowed_hosts", default=[]) or []
    if isinstance(extra, list):
        hosts.update(str(h).strip().lower() for h in extra if str(h).strip())
    return hosts


def host_is_allowed(host_header: str | None) -> bool:
    """Con bind en loopback solo se aceptan hosts locales; en modo servidor manda el token."""
    if not is_loopback_bind():
        return True
    host = str(host_header or "").strip().lower()
    if not host:
        return False
    return host in allowed_hosts()


def is_public_route(method: str, path: str) -> bool:
    method = method.upper()
    if (method, path) in PUBLIC_ROUTES:
        return True
    return any(method == m and path.startswith(prefix) for m, prefix in PUBLIC_PREFIXES)


def extract_token(headers: Any, query_params: Any | None = None) -> str:
    auth = str(headers.get("authorization") or "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    custom = str(headers.get(TOKEN_HEADER) or "").strip()
    if custom:
        return custom
    if query_params is not None:
        return str(query_params.get("token") or "").strip()
    return ""


def browser_origin_is_untrusted(origin: str | None, *, allow_extensions: bool = False) -> bool:
    """
    True si el Origin viene de una página web ajena a la app.

    Sin Origin = cliente nativo (CLI, puente del editor). `file://` = la UI de
    Electron. `null` NO es confiable: lo envían los iframes con sandbox de
    cualquier web.
    """
    value = str(origin or "").strip().lower()
    if not value or value == "file://":
        return False
    if allow_extensions and value.startswith(("chrome-extension://", "moz-extension://", "safari-web-extension://")):
        return False
    port = _server_port()
    if value in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
        return False
    return True
