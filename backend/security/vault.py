"""API keys y tokens de G-Mini.

Lectura, en este orden:
1. Variable de entorno GMINI_KEY_<NOMBRE> (por ejemplo GMINI_KEY_OPENAI_API),
   pensada para servidores y contenedores.
2. Almacén de credenciales del sistema: Administrador de credenciales de
   Windows, Llavero de macOS o Secret Service en Linux con escritorio.
3. data/runtime/secrets.json, solo si el sistema no tiene almacén (un VPS o
   una Raspberry Pi sin escritorio). Queda con permisos 600 y el agente no
   puede leerlo por la vía de archivos.
4. La variable estándar del proveedor (OPENAI_API_KEY, ANTHROPIC_API_KEY...).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

import keyring
from keyring.errors import NoKeyringError, PasswordDeleteError
from loguru import logger

SERVICE_NAME = "gmini-agent"

# Solo nombres que identifican al proveedor sin ambigüedad (GOOGLE_API_KEY o
# GITHUB_TOKEN suelen ser de otras cosas).
STANDARD_ENV = {
    "openai_api": "OPENAI_API_KEY",
    "anthropic_api": "ANTHROPIC_API_KEY",
    "google_api": "GEMINI_API_KEY",
    "cohere_api": "COHERE_API_KEY",
    "xai_api": "XAI_API_KEY",
    "deepseek_api": "DEEPSEEK_API_KEY",
    "groq_api": "GROQ_API_KEY",
    "mistral_api": "MISTRAL_API_KEY",
    "perplexity_api": "PERPLEXITY_API_KEY",
    "moonshot_api": "MOONSHOT_API_KEY",
    "dashscope_api": "DASHSCOPE_API_KEY",
    "cerebras_api": "CEREBRAS_API_KEY",
    "openrouter_api": "OPENROUTER_API_KEY",
    "together_api": "TOGETHER_API_KEY",
    "fireworks_api": "FIREWORKS_API_KEY",
    "deepinfra_api": "DEEPINFRA_API_KEY",
    "elevenlabs_api": "ELEVENLABS_API_KEY",
}

_lock = threading.Lock()
_warned_file_store = False


def env_name(vault_name: str) -> str:
    """Variable GMINI_KEY_* que sobreescribe una key: openai_api -> GMINI_KEY_OPENAI_API."""
    clean = "".join(ch if ch.isalnum() else "_" for ch in vault_name.upper())
    return f"GMINI_KEY_{clean}"


def secrets_file() -> Path:
    from backend.config import ROOT_DIR

    return ROOT_DIR / "data" / "runtime" / "secrets.json"


def _read_file_store() -> dict[str, str]:
    path = secrets_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning(f"vault: no pude leer {path.name}: {exc}")
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _write_file_store(data: dict[str, str]) -> None:
    path = secrets_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".secrets-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _note_file_store() -> None:
    global _warned_file_store
    if not _warned_file_store:
        _warned_file_store = True
        logger.info(f"vault: el sistema no tiene almacén de credenciales; uso {secrets_file()} (permisos 600)")


def get_api_key(vault_name: str) -> str | None:
    if not vault_name:
        return None
    explicit = os.environ.get(env_name(vault_name), "").strip()
    if explicit:
        return explicit
    stored = None
    try:
        stored = keyring.get_password(SERVICE_NAME, vault_name)
    except NoKeyringError:
        stored = _read_file_store().get(vault_name)
    except Exception as exc:
        logger.debug(f"vault: keyring falló al leer {vault_name}: {exc}")
    if stored:
        return stored
    standard = STANDARD_ENV.get(vault_name)
    return (os.environ.get(standard, "").strip() or None) if standard else None


def set_api_key(vault_name: str, api_key: str) -> None:
    try:
        keyring.set_password(SERVICE_NAME, vault_name, api_key)
    except NoKeyringError:
        _note_file_store()
        with _lock:
            data = _read_file_store()
            data[vault_name] = api_key
            _write_file_store(data)


def delete_api_key(vault_name: str) -> None:
    try:
        keyring.delete_password(SERVICE_NAME, vault_name)
    except (PasswordDeleteError, NoKeyringError):
        pass
    with _lock:
        data = _read_file_store()
        if data.pop(vault_name, None) is not None:
            _write_file_store(data)


def backend_name() -> str:
    """Dónde quedan las keys guardadas desde la app: 'keyring:<backend>' o 'file'."""
    try:
        current = keyring.get_keyring()
    except Exception:
        return "file"
    if type(current).__module__.endswith(".fail"):
        return "file"
    return f"keyring:{type(current).__module__.rsplit('.', 1)[-1]}"
