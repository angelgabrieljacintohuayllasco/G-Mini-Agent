"""
G-Mini Agent — Gestión de configuración.
Carga config.default.yaml con merge de config.user.yaml.
Las API keys van al almacén de credenciales del sistema (ver backend/security/vault.py).
"""

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from backend.security import vault
from backend.security.vault import SERVICE_NAME  # noqa: F401  (compatibilidad)

CODE_DIR = Path(__file__).resolve().parent.parent
# GMINI_HOME separa los datos del programa: con una instalación de solo
# lectura (AppImage, deb, Program Files) o en Docker, la config, las bases y
# los archivos generados viven ahí. Sin la variable todo queda junto al código.
_GMINI_HOME = os.environ.get("GMINI_HOME", "").strip()
ROOT_DIR = Path(_GMINI_HOME).expanduser().resolve() if _GMINI_HOME else CODE_DIR
DEFAULT_CONFIG = CODE_DIR / "config.default.yaml"
USER_CONFIG = ROOT_DIR / "config.user.yaml"
USER_CONFIG_HEADER = (
    "# Tus ajustes de G-Mini: solo lo que difiere de config.default.yaml.\n"
    "# La app lo escribe sola; si lo editas a mano, respeta el formato YAML.\n"
)
# Recursos que trae el programa dentro de data/ (se copian a GMINI_HOME al arrancar).
SHIPPED_DATA = ("prompts", "skills", "agent_skills/bundled", "crews", "commands", "models.yaml", "realtime_models.yaml")


def sync_shipped_data(code_dir: Path = CODE_DIR, home_dir: Path = ROOT_DIR) -> None:
    """Copia a GMINI_HOME los prompts, catálogos y skills incluidas del programa.

    Se repite en cada arranque para que una actualización llegue a la carpeta
    de datos; lo que el usuario cambia (overrides de prompts, skills propias)
    vive en la config o en otras carpetas y no se pisa.
    """
    if home_dir == code_dir:
        return
    for rel in SHIPPED_DATA:
        src, dst = code_dir / "data" / rel, home_dir / "data" / rel
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge recursivo. Los valores de override sobreescriben base."""
    merged = base.copy()
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class Config:
    """Singleton de configuración."""

    _instance: "Config | None" = None
    _data: dict[str, Any] = {}

    def __new__(cls) -> "Config":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        if ROOT_DIR != CODE_DIR:
            ROOT_DIR.mkdir(parents=True, exist_ok=True)
            sync_shipped_data()
        default_text = DEFAULT_CONFIG.read_text(encoding="utf-8")
        defaults = yaml.safe_load(default_text) or {}

        # config.user.yaml guarda solo lo que el usuario cambió, así los
        # defaults nuevos de una actualización le llegan.
        if not USER_CONFIG.exists():
            USER_CONFIG.write_text(USER_CONFIG_HEADER, encoding="utf-8")
        user_text = USER_CONFIG.read_text(encoding="utf-8")
        user_data = yaml.safe_load(user_text) or {}

        self._data = _deep_merge(defaults, user_data)
        # La versión es la del programa, no un ajuste.
        default_version = (defaults.get("app") or {}).get("version")
        if default_version and isinstance(self._data.get("app"), dict):
            self._data["app"]["version"] = default_version

        # Las instalaciones viejas copiaban config.default.yaml entero (con su
        # encabezado) y eso congelaba todos los defaults: se reduce a lo que
        # difiere. Lo que el usuario cambió a mano se conserva.
        first_line = default_text.split("\n", 1)[0].strip()
        if first_line.startswith("#") and user_text.lstrip("\ufeff").startswith(first_line):
            self._save_user_config()

    def reload(self) -> None:
        self._load()

    def get(self, *keys: str, default: Any = None) -> Any:
        """Accede a claves anidadas. Ej: config.get('providers', 'openai', 'base_url')"""
        current = self._data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def set(self, *keys: str, value: Any) -> None:
        """Establece un valor anidado y persiste en config.user.yaml."""
        # Update in memory
        current = self._data
        for key in keys[:-1]:
            if key not in current or not isinstance(current[key], dict):
                current[key] = {}
            current = current[key]
        current[keys[-1]] = value

        # Persist
        self._save_user_config()

    def unset(self, *keys: str) -> None:
        """Elimina un valor anidado y persiste el cambio."""
        if not keys:
            return

        stack: list[tuple[dict[str, Any], str]] = []
        current = self._data
        for key in keys[:-1]:
            if not isinstance(current, dict) or key not in current:
                return
            stack.append((current, key))
            current = current[key]

        if not isinstance(current, dict) or keys[-1] not in current:
            return

        del current[keys[-1]]

        for parent, key in reversed(stack):
            child = parent.get(key)
            if isinstance(child, dict) and not child:
                del parent[key]

        self._save_user_config()

    def _save_user_config(self) -> None:
        """Guarda solo los deltas del usuario respecto a defaults."""
        try:
            with open(DEFAULT_CONFIG, "r", encoding="utf-8") as f:
                defaults = yaml.safe_load(f) or {}
        except Exception:
            defaults = {}

        user_delta = self._compute_delta(defaults, self._data)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=USER_CONFIG.parent, suffix=".tmp", prefix="config_"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                yaml.dump(user_delta, f, default_flow_style=False, allow_unicode=True)
            os.replace(tmp_path, USER_CONFIG)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def _compute_delta(defaults: dict, current: dict) -> dict:
        """Retorna solo las claves de current que difieren de defaults."""
        delta = {}
        for key, value in current.items():
            if key not in defaults:
                delta[key] = value
            elif isinstance(value, dict) and isinstance(defaults.get(key), dict):
                sub = Config._compute_delta(defaults[key], value)
                if sub:
                    delta[key] = sub
            elif value != defaults.get(key):
                delta[key] = value
        return delta

    @property
    def data(self) -> dict:
        return self._data

    # ── API keys (backend/security/vault.py) ─────────────────────

    @staticmethod
    def get_api_key(vault_name: str) -> str | None:
        """API key por su nombre de vault: entorno, almacén del sistema o archivo protegido."""
        return vault.get_api_key(vault_name)

    @staticmethod
    def set_api_key(vault_name: str, api_key: str) -> None:
        vault.set_api_key(vault_name, api_key)

    @staticmethod
    def delete_api_key(vault_name: str) -> None:
        vault.delete_api_key(vault_name)


# Instancia global
config = Config()
