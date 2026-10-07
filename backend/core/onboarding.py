"""
G-Mini Agent — Asistente de primera ejecución.

Pasos generados por el backend (la UI solo los dibuja): idioma, identidad del
agente, proveedor + key, modelo, autonomía, voz y permiso para armar el perfil.
Cada paso muestra por defecto lo que ya está configurado y solo escribe lo que
el usuario cambió.

Contrato con la UI (Socket.IO y REST):
  paso  = {id, title, help, step_number, total_steps, status: "running",
           fields: [{name, type, label, default, optional, placeholder, maxlength,
                     options: [{value, label}]}],
           notice: {kind: "info"|"warning"|"error", text} | None}
  type  = select | toggle | secret | text | textarea
  Un error de validación devuelve el MISMO paso con notice.kind = "error".
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from backend.config import ROOT_DIR, config

PERSONALITY_PRESETS = {
    "": "Neutral (sin personalidad extra)",
    "Directo, técnico y sin rodeos.": "Directo y técnico",
    "Cercano, amable y paciente; explica con ejemplos.": "Cercano y paciente",
    "Formal y preciso, con tono profesional.": "Formal",
    "Relajado y con humor ligero, sin perder la precisión.": "Con humor ligero",
}
LOCAL_PROVIDERS = ("ollama", "lmstudio")
SETUP_PROVIDERS = (
    "vertex", "google", "openai", "anthropic", "xai", "deepseek", "groq", "mistral",
    "openrouter", "moonshot", "dashscope", "zai", "ollama", "lmstudio",
)


def _opt(value: str, label: str) -> dict[str, str]:
    return {"value": value, "label": label}


class OnboardingService:
    def __init__(
        self,
        getter: Callable | None = None,
        setter: Callable | None = None,
        key_setter: Callable[[str, str], None] | None = None,
        on_provider_saved: Callable[[str], bool] | None = None,
    ) -> None:
        self._get = getter or config.get
        self._set = setter or config.set
        self._key_setter = key_setter or config.set_api_key
        self._on_provider_saved = on_provider_saved
        self._lock = threading.Lock()
        self._idx = 0
        self._status = "idle"
        self._notice: dict[str, str] | None = None
        self._chosen_provider = ""

    # ── Pasos ────────────────────────────────────────────────────────

    @property
    def steps(self) -> list[dict[str, Any]]:
        return [self._build_step(step_id) for step_id in self._step_ids()]

    @staticmethod
    def _step_ids() -> list[str]:
        return ["welcome", "identity", "provider", "model", "autonomy", "voice", "profile_optin"]

    def _current_provider(self) -> str:
        return self._chosen_provider or str(self._get("model_router", "default_provider", default="vertex") or "vertex")

    def _build_step(self, step_id: str) -> dict[str, Any]:
        from backend.core import identity
        from backend.providers import registry

        if step_id == "welcome":
            return {
                "id": "welcome", "title": "Hola. Vamos a dejar todo listo",
                "help": "Son unos pocos pasos y puedes cambiar cualquier cosa después en Ajustes.",
                "fields": [{
                    "name": "language", "type": "select", "label": "Idioma de las respuestas",
                    "default": identity.language(self._get),
                    "options": [_opt(code, name.capitalize()) for code, name in identity.LANGUAGES.items()],
                }],
            }
        if step_id == "identity":
            soul = identity.personality(self._get)
            presets = dict(PERSONALITY_PRESETS)
            if soul and soul not in presets:
                presets[soul] = "La que ya tienes"
            return {
                "id": "identity", "title": "¿Cómo se llama tu agente?",
                "help": "Ponle el nombre que quieras; también puedes pedirle en el chat que se cambie el nombre.",
                "fields": [
                    {"name": "agent_name", "type": "text", "label": "Nombre del agente",
                     "default": identity.agent_name(self._get), "maxlength": identity.MAX_NAME_LENGTH},
                    {"name": "personality", "type": "select", "label": "Personalidad", "default": soul,
                     "options": [_opt(value, label) for value, label in presets.items()]},
                    {"name": "user_name", "type": "text", "label": "¿Cómo te llamas? (opcional)",
                     "default": "", "optional": True, "maxlength": 40},
                ],
            }
        if step_id == "provider":
            current = self._current_provider()
            options = []
            for pid in SETUP_PROVIDERS:
                spec = registry.get_spec(pid)
                if spec is not None:
                    options.append(_opt(pid, spec.label))
            return {
                "id": "provider", "title": "¿Con qué IA trabajará?",
                "help": (
                    "Las keys se guardan en el Administrador de credenciales de Windows, nunca en archivos. "
                    "Vertex AI usa tu sesión de gcloud; Ollama y LM Studio corren en tu PC sin key."
                ),
                "fields": [
                    {"name": "provider", "type": "select", "label": "Proveedor", "default": current, "options": options},
                    {"name": "api_key", "type": "secret", "label": "API key", "optional": True,
                     "placeholder": "Déjala vacía si ya la configuraste o no hace falta"},
                ],
            }
        if step_id == "model":
            provider = self._current_provider()
            spec = registry.get_spec(provider)
            models = list(spec.default_models) if spec else []
            current_model = str(self._get("model_router", "default_model", default="") or "")
            if current_model and current_model not in models and provider == self._get("model_router", "default_provider"):
                models.insert(0, current_model)
            return {
                "id": "model", "title": "Modelo principal",
                "help": "El que usará para conversar y planificar. Las tareas de fondo usan uno más económico.",
                "fields": [{
                    "name": "model", "type": "select", "label": f"Modelo de {spec.label if spec else provider}",
                    "default": current_model if current_model in models else (models[0] if models else ""),
                    "options": [_opt(m, m) for m in models],
                }],
            }
        if step_id == "autonomy":
            return {
                "id": "autonomy", "title": "¿Cuánta libertad le das?",
                "help": "Autonomía: cuánta iniciativa toma. Permisos: qué hace sin pedirte confirmación.",
                "fields": [
                    {"name": "autonomy", "type": "select", "label": "Autonomía",
                     "default": str(self._get("agent", "autonomy", default="media")),
                     "options": [_opt("baja", "Baja: hace solo lo que pides"), _opt("media", "Media: propone y avanza"),
                                 _opt("alta", "Alta: resuelve de punta a punta")]},
                    {"name": "permission", "type": "select", "label": "Permisos",
                     "default": str(self._get("agent", "autonomy_level", default="supervisado")),
                     "options": [_opt("asistido", "Asistido: confirma casi todo"),
                                 _opt("supervisado", "Supervisado: confirma lo delicado"),
                                 _opt("libre", "Libre: confirma solo lo crítico")]},
                ],
            }
        if step_id == "voice":
            return {
                "id": "voice", "title": "Voz",
                "help": "Puede leer sus respuestas en voz alta. Las voces neuronales de Edge son gratuitas.",
                "fields": [{"name": "voice", "type": "toggle", "label": "Responder también con voz",
                            "default": bool(self._get("voice", "auto_tts", default=False))}],
            }
        if step_id == "profile_optin":
            return {
                "id": "profile_optin", "title": "¿Te conozco un poco más?",
                "help": (
                    "Si aceptas, recordará tu nombre, a qué te dedicas y cómo te gusta trabajar, y en la primera "
                    "charla te preguntará un par de cosas. Puedes ver y borrar lo que recuerda en Ajustes > Memoria."
                ),
                "fields": [{"name": "profile_build", "type": "toggle", "label": "Sí, recuerda lo que te cuente de mí",
                            "default": str(self._get("onboarding", "profile_build", default="ask")) != "off"}],
            }
        raise KeyError(step_id)

    # ── Máquina de estados ───────────────────────────────────────────

    def _payload(self) -> dict[str, Any]:
        ids = self._step_ids()
        step = self._build_step(ids[self._idx])
        return {**step, "status": "running", "step_number": self._idx + 1,
                "total_steps": len(ids), "notice": self._notice}

    def start(self, *, rerun: bool = False) -> dict[str, Any]:
        with self._lock:
            if not rerun and not is_first_run(self._get):
                return {"status": "done", "already_completed": True}
            self._idx, self._status, self._notice, self._chosen_provider = 0, "running", None, ""
            return self._payload()

    def current(self) -> dict[str, Any]:
        with self._lock:
            return self._payload() if self._status == "running" else {"status": self._status}

    def back(self) -> dict[str, Any]:
        with self._lock:
            if self._status != "running":
                return {"status": self._status}
            self._idx, self._notice = max(0, self._idx - 1), None
            return self._payload()

    def answer(self, step_id: str, value: dict[str, Any] | None) -> dict[str, Any]:
        with self._lock:
            if self._status != "running":
                return {"status": self._status}
            ids = self._step_ids()
            if step_id != ids[self._idx]:
                # Doble clic o reenvío del paso anterior: se devuelve el paso actual.
                return self._payload()
            value = value if isinstance(value, dict) else {}
            self._notice = None
            if not value.get("skip"):
                try:
                    notice = self._apply(step_id, value)
                except ValueError as exc:
                    self._notice = {"kind": "error", "text": str(exc)}
                    return self._payload()
                self._notice = notice
            self._idx += 1
            if self._idx >= len(ids):
                self._status = "done"
                self._set("onboarding", "completed", value=True)
                return {"status": "done"}
            return self._payload()

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            self._status = "cancelled"
            import time

            self._set("onboarding", "dismissed_at", value=int(time.time()))
            return {"status": "cancelled"}

    # ── Aplicar respuestas (solo lo que cambió) ──────────────────────

    def _set_if_changed(self, section: str, key: str, value: Any, current: Any = None) -> None:
        """Escribe solo si difiere del valor efectivo (el que el paso mostró por defecto)."""
        if current is None:
            current = self._get(section, key)
        if current != value:
            self._set(section, key, value=value)

    def _apply(self, step_id: str, value: dict[str, Any]) -> dict[str, str] | None:
        from backend.core import identity
        from backend.providers import registry

        if step_id == "welcome":
            lang = str(value.get("language") or "es")
            if lang not in identity.LANGUAGES:
                raise ValueError("Idioma no disponible.")
            self._set_if_changed("app", "language", lang, identity.language(self._get))
        elif step_id == "identity":
            name = identity.validate_name(value.get("agent_name") or identity.agent_name(self._get))
            self._set_if_changed("agent", "name", name, identity.agent_name(self._get))
            soul = " ".join(str(value.get("personality") or "").split())[: identity.MAX_SOUL_LENGTH]
            self._set_if_changed("agent", "soul", soul, identity.personality(self._get))
            user_name = " ".join(str(value.get("user_name") or "").split())[:40]
            if user_name:
                _remember_user_name(user_name)
        elif step_id == "provider":
            provider = str(value.get("provider") or "")
            spec = registry.get_spec(provider)
            if spec is None:
                raise ValueError("Elige un proveedor de la lista.")
            key = str(value.get("api_key") or "").strip()
            if key and spec.api_key_vault and not spec.local:
                self._key_setter(spec.api_key_vault, key)
            self._chosen_provider = provider
            ready = self._on_provider_saved(provider) if self._on_provider_saved else True
            if not ready:
                hint = (
                    "No encontré credenciales de Google Cloud: ejecuta `gcloud auth application-default login`."
                    if provider == "vertex" else
                    f"{spec.label} no responde en tu PC: ábrelo antes de chatear." if provider in LOCAL_PROVIDERS else
                    f"Falta la API key de {spec.label}. Puedes agregarla luego en Ajustes."
                )
                return {"kind": "warning", "text": hint}
        elif step_id == "model":
            provider = self._current_provider()
            model = str(value.get("model") or "").strip()
            if not model:
                raise ValueError("Elige un modelo.")
            self._set_if_changed("model_router", "default_provider", provider)
            self._set_if_changed("model_router", "default_model", model)
            from backend.core.embeddings import reset_embedder

            reset_embedder()  # la memoria elige embeddings según el proveedor del chat
        elif step_id == "autonomy":
            autonomy = str(value.get("autonomy") or "media")
            permission = str(value.get("permission") or "supervisado")
            if autonomy not in ("baja", "media", "alta") or permission not in ("asistido", "supervisado", "libre"):
                raise ValueError("Valor de autonomía no válido.")
            self._set_if_changed("agent", "autonomy", autonomy, str(self._get("agent", "autonomy", default="media")))
            self._set_if_changed(
                "agent", "autonomy_level", permission, str(self._get("agent", "autonomy_level", default="supervisado"))
            )
        elif step_id == "voice":
            enabled = bool(value.get("voice"))
            self._set_if_changed("voice", "auto_tts", enabled, bool(self._get("voice", "auto_tts", default=False)))
            if enabled:
                self._set_if_changed("voice", "enabled", True, bool(self._get("voice", "enabled", default=False)))
        elif step_id == "profile_optin":
            self._set_if_changed(
                "onboarding", "profile_build", "ask" if value.get("profile_build") else "off", profile_build_mode(self._get)
            )
        return None


def _remember_user_name(user_name: str) -> None:
    """El nombre que el usuario escribió en el asistente va directo a la memoria."""
    try:
        from backend.core.memory_ltm import get_ltm

        ltm = get_ltm()
        text = f"El usuario se llama {user_name}."
        if ltm.find_same_text(text) is None:
            ltm.store(text, "fact", importance=0.95, metadata={"source": "onboarding"})
    except Exception as exc:
        logger.warning(f"No se pudo guardar el nombre del usuario: {exc}")


# ── Estado global ───────────────────────────────────────────────────────

def is_first_run(getter: Callable | None = None) -> bool:
    g = getter or config.get
    return not g("onboarding", "completed", default=False)


def should_offer(getter: Callable | None = None) -> bool:
    """Mostrar el asistente al conectar: primera ejecución y no descartado."""
    g = getter or config.get
    return is_first_run(g) and not g("onboarding", "dismissed_at", default=None)


def _has_prior_usage(data_dir: Path | None = None) -> bool:
    data_dir = data_dir or (ROOT_DIR / "data")
    db = data_dir / "memory.db"
    if db.exists():
        try:
            with sqlite3.connect(str(db), timeout=5) as conn:
                if conn.execute("SELECT 1 FROM sessions WHERE message_count > 0 LIMIT 1").fetchone():
                    return True
        except Exception:
            pass
    try:
        from backend.providers import registry

        for pid in registry.provider_ids():
            spec = registry.get_spec(pid)
            if spec and spec.api_key_vault and config.get_api_key(spec.api_key_vault):
                return True
    except Exception:
        pass
    return False


def migrate_existing_install(getter: Callable | None = None, setter: Callable | None = None,
                             data_dir: Path | None = None) -> bool:
    """Quien ya usaba G-Mini antes del asistente no debe verlo ni perder su config."""
    g = getter or config.get
    s = setter or config.set
    if not is_first_run(g):
        return False
    if _has_prior_usage(data_dir):
        s("onboarding", "completed", value=True)
        logger.info("Instalación existente: asistente inicial marcado como completado")
        return True
    return False


def profile_build_mode(getter: Callable | None = None) -> str:
    g = getter or config.get
    mode = g("onboarding", "profile_build", default="ask")
    return "off" if isinstance(mode, str) and mode.strip().lower() == "off" else "ask"


def profile_build_directive() -> str:
    return (
        "[AVISO DE UNA SOLA VEZ: el usuario aceptó que lo conozcas mejor. Después de responder a lo que "
        "pidió, ofrece en una o dos frases armar un perfil corto (cómo se llama, a qué se dedica, cómo le "
        "gusta que trabajes) y aclara que puede decir que no o hacerlo después. Si acepta, pregunta de a "
        "poco y de forma natural; antes de buscar algo sobre él fuera de esta conversación, pide permiso. "
        "Si dice que no, sigue normalmente y no vuelvas a ofrecerlo.]"
    )


def is_seen(getter: Callable | None = None, flag: str = "") -> bool:
    g = getter or config.get
    return bool(g("onboarding", "seen", flag, default=False))


def mark_seen(setter: Callable | None = None, flag: str = "") -> None:
    s = setter or config.set
    s("onboarding", "seen", flag, value=True)


_onboarding: OnboardingService | None = None


def get_onboarding() -> OnboardingService:
    global _onboarding
    if _onboarding is None:
        _onboarding = OnboardingService(on_provider_saved=_reload_provider)
    return _onboarding


def _reload_provider(provider: str) -> bool:
    """Recrea el provider en el router del agente y dice si quedó configurado."""
    try:
        from backend.api.websocket_handler import _agent_core

        router = getattr(_agent_core, "_router", None)
        if router is not None:
            router.reload_provider(provider)
            return router.is_available(provider)
    except Exception as exc:
        logger.debug(f"onboarding: no se pudo recargar {provider}: {exc}")
    return True
