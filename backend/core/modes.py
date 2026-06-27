"""
Predefined operating modes and capability profiles for G-Mini Agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.config import config


@dataclass(frozen=True)
class AgentMode:
    key: str
    name: str
    description: str
    behavior_prompt: str
    icon: str = ""
    system_prompt: str = ""
    is_custom: bool = False
    allowed_capabilities: tuple[str, ...] = ()
    restricted_capabilities: tuple[str, ...] = ()
    requires_scope_confirmation: bool = False


CAPABILITY_LABELS: dict[str, str] = {
    "observe": "lectura y observacion",
    "desktop_control": "control de escritorio",
    "browser_control": "control de navegador",
    "browser_dom": "lectura DOM y contenido web",
    "browser_download": "descarga de archivos",
    "file_scan": "escaneo de archivos",
    "mobile_control": "control Android/ADB",
    "development": "acciones tecnicas y de desarrollo",
    "marketing_ops": "operacion de marketing",
    "sales_ops": "operacion comercial",
    "research_ops": "investigacion y perfilado",
    "content_ops": "produccion de contenido",
    "guardian_ops": "supervision y proteccion",
    "gaming_ops": "automatizacion de juegos",
    "offensive_security": "seguridad ofensiva controlada",
}


PREDEFINED_MODES: dict[str, AgentMode] = {
    "normal": AgentMode(
        key="normal",
        name="Normal",
        icon="general",
        description="Asistente general equilibrado para tareas comunes.",
        behavior_prompt=(
            "Prioriza utilidad general, claridad, verificacion y ejecucion segura. "
            "Si una accion tiene riesgo o puede afectar archivos, cuentas o descargas, escalala."
        ),
        allowed_capabilities=(
            "observe",
            "desktop_control",
            "browser_control",
            "browser_dom",
            "browser_download",
            "file_scan",
            "mobile_control",
            "development",
        ),
        restricted_capabilities=("offensive_security",),
    ),
    "programador": AgentMode(
        key="programador",
        name="Programador",
        icon="code",
        description="Asistente de desarrollo, debugging y automatizacion tecnica.",
        behavior_prompt=(
            "Piensa como un ingeniero senior. Prioriza precision tecnica, diagnostico, logs, pruebas, "
            "automatizacion reproducible y cambios verificables."
        ),
        allowed_capabilities=(
            "observe",
            "desktop_control",
            "browser_control",
            "browser_dom",
            "browser_download",
            "file_scan",
            "mobile_control",
            "development",
        ),
        restricted_capabilities=("offensive_security",),
    ),
    "marketero": AgentMode(
        key="marketero",
        name="Marketero",
        icon="campaign",
        description="Especialista en growth, anuncios y contenido promocional.",
        behavior_prompt=(
            "Piensa como operador de marketing digital. Prioriza conversion, creatividad, pruebas A/B, "
            "copys, hooks y ejecucion en plataformas sociales."
        ),
        allowed_capabilities=(
            "observe",
            "desktop_control",
            "browser_control",
            "browser_dom",
            "browser_download",
            "file_scan",
            "marketing_ops",
            "content_ops",
            "development",
        ),
        restricted_capabilities=("offensive_security",),
    ),
    "asesor_ventas": AgentMode(
        key="asesor_ventas",
        name="Asesor de Ventas",
        icon="sales",
        description="Seguimiento comercial, leads y cierre con supervision.",
        behavior_prompt=(
            "Actua como closer y operador comercial. Prioriza claridad, seguimiento, CRM y mensajes "
            "persuasivos sin inventar datos ni compromisos no autorizados."
        ),
        allowed_capabilities=(
            "observe",
            "desktop_control",
            "browser_control",
            "browser_dom",
            "sales_ops",
            "development",
        ),
        restricted_capabilities=("browser_download", "offensive_security"),
    ),
    "investigador": AgentMode(
        key="investigador",
        name="Investigador + Perfilador",
        icon="search",
        description="Modo analitico para investigacion, OSINT y perfiles.",
        behavior_prompt=(
            "Prioriza verificacion, trazabilidad de evidencia, comparacion de fuentes y reportes "
            "estructurados antes de concluir."
        ),
        allowed_capabilities=(
            "observe",
            "desktop_control",
            "browser_control",
            "browser_dom",
            "browser_download",
            "file_scan",
            "research_ops",
            "development",
        ),
        restricted_capabilities=("offensive_security",),
    ),
    "pentester": AgentMode(
        key="pentester",
        name="Pentester Etico + Hacker",
        icon="shield",
        description="Seguridad ofensiva solo con autorizacion explicita y scope valido.",
        behavior_prompt=(
            "Opera solo sobre activos propios, laboratorios, CTF o scopes explicitamente autorizados. "
            "Si el alcance no esta confirmado, detente y pide confirmacion."
        ),
        allowed_capabilities=(
            "observe",
            "desktop_control",
            "browser_control",
            "browser_dom",
            "browser_download",
            "file_scan",
            "development",
            "offensive_security",
        ),
        requires_scope_confirmation=True,
    ),
    "tutor": AgentMode(
        key="tutor",
        name="Tutor/Coach",
        icon="education",
        description="Ensenanza, planes de estudio y seguimiento guiado.",
        behavior_prompt=(
            "Enfocate en explicar, estructurar pasos, medir progreso y acompanar al usuario "
            "sin ejecutar cambios riesgosos innecesarios."
        ),
        allowed_capabilities=(
            "observe",
            "desktop_control",
            "browser_control",
            "browser_dom",
        ),
        restricted_capabilities=("browser_download", "offensive_security"),
    ),
    "padre_digital": AgentMode(
        key="padre_digital",
        name="Padre Digital",
        icon="family",
        description="Supervision de productividad, horarios y limites.",
        behavior_prompt=(
            "Prioriza bienestar, enfoque, recordatorios y reduccion de distracciones. "
            "Evita acciones de riesgo y privilegia lectura o confirmacion humana."
        ),
        allowed_capabilities=(
            "observe",
            "desktop_control",
            "browser_control",
            "browser_dom",
            "guardian_ops",
        ),
        restricted_capabilities=("browser_download", "offensive_security"),
    ),
    "hermano_protector": AgentMode(
        key="hermano_protector",
        name="Hermano Protector",
        icon="protection",
        description="Bienestar, seguridad y acompanamiento preventivo.",
        behavior_prompt=(
            "Prioriza seguridad, bienestar y alertas preventivas. Evita acciones destructivas "
            "o invasivas salvo confirmacion explicita."
        ),
        allowed_capabilities=(
            "observe",
            "desktop_control",
            "browser_control",
            "browser_dom",
            "file_scan",
            "guardian_ops",
        ),
        restricted_capabilities=("browser_download", "offensive_security"),
    ),
    "gamer": AgentMode(
        key="gamer",
        name="Gamer",
        icon="gamepad",
        description="Automatizacion de juegos basada en vision y control.",
        behavior_prompt=(
            "Prioriza baja latencia, lectura visual del estado del juego y acciones repetibles. "
            "Evita salir del contexto del juego sin necesidad."
        ),
        allowed_capabilities=(
            "observe",
            "desktop_control",
            "mobile_control",
            "gaming_ops",
        ),
        restricted_capabilities=("browser_download", "offensive_security", "browser_dom"),
    ),
    "creador": AgentMode(
        key="creador",
        name="Creador de Contenido",
        icon="clapperboard",
        description="Produccion multimedia, guiones, assets y publicaciones.",
        behavior_prompt=(
            "Prioriza velocidad creativa, storytelling, ganchos, formatos multiplataforma y entregables "
            "listos para publicar."
        ),
        allowed_capabilities=(
            "observe",
            "desktop_control",
            "browser_control",
            "browser_dom",
            "browser_download",
            "file_scan",
            "content_ops",
            "development",
        ),
        restricted_capabilities=("offensive_security",),
    ),
}


DEFAULT_MODE_KEY = "normal"


def _normalize_mode_key(mode_key: str | None) -> str:
    key = str(mode_key or DEFAULT_MODE_KEY).strip().lower()
    return key or DEFAULT_MODE_KEY


def _normalize_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def _normalize_capability_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        capability = str(item or "").strip()
        if not capability or capability in seen:
            continue
        seen.add(capability)
        normalized.append(capability)
    return tuple(normalized)


def _load_custom_modes() -> dict[str, AgentMode]:
    raw_custom_modes = config.get("modes", "custom", default={})
    if not isinstance(raw_custom_modes, dict):
        return {}

    custom_modes: dict[str, AgentMode] = {}
    sorted_items = sorted(
        raw_custom_modes.items(),
        key=lambda item: _normalize_mode_key(str(item[0])),
    )
    for raw_key, raw_mode in sorted_items:
        mode_key = _normalize_mode_key(str(raw_key))
        if mode_key in PREDEFINED_MODES or mode_key in custom_modes:
            continue
        if not isinstance(raw_mode, dict):
            continue

        custom_modes[mode_key] = AgentMode(
            key=mode_key,
            name=_normalize_text(raw_mode.get("name"), mode_key),
            description=_normalize_text(raw_mode.get("description")),
            behavior_prompt=_normalize_text(raw_mode.get("behavior_prompt")),
            icon=_normalize_text(raw_mode.get("icon")),
            system_prompt=_normalize_text(raw_mode.get("system_prompt")),
            is_custom=True,
            allowed_capabilities=_normalize_capability_list(raw_mode.get("allowed_capabilities")),
            restricted_capabilities=_normalize_capability_list(raw_mode.get("restricted_capabilities")),
            requires_scope_confirmation=bool(raw_mode.get("requires_scope_confirmation", False)),
        )
    return custom_modes


def _get_all_modes() -> dict[str, AgentMode]:
    modes = dict(PREDEFINED_MODES)
    modes.update(_load_custom_modes())
    return modes


def list_modes() -> list[dict[str, object]]:
    return [serialize_mode(mode) for mode in _get_all_modes().values()]


def get_mode(mode_key: str | None) -> AgentMode:
    key = _normalize_mode_key(mode_key)
    return _get_all_modes().get(key, PREDEFINED_MODES[DEFAULT_MODE_KEY])


def serialize_mode(mode: AgentMode) -> dict[str, object]:
    return {
        "key": mode.key,
        "name": mode.name,
        "description": mode.description,
        "icon": mode.icon,
        "behavior_prompt": mode.behavior_prompt,
        "system_prompt": mode.system_prompt,
        "is_custom": mode.is_custom,
        "allowed_capabilities": list(mode.allowed_capabilities),
        "allowed_labels": [CAPABILITY_LABELS.get(cap, cap) for cap in mode.allowed_capabilities],
        "restricted_capabilities": list(mode.restricted_capabilities),
        "restricted_labels": [CAPABILITY_LABELS.get(cap, cap) for cap in mode.restricted_capabilities],
        "requires_scope_confirmation": mode.requires_scope_confirmation,
    }


def resolve_mode_capability_scope(
    parent_mode_key: str | None,
    child_mode_key: str | None,
) -> dict[str, object]:
    parent_mode = get_mode(parent_mode_key)
    child_mode = get_mode(child_mode_key)

    parent_allowed = set(parent_mode.allowed_capabilities)
    child_allowed = set(child_mode.allowed_capabilities)
    effective_allowed = tuple(
        capability
        for capability in child_mode.allowed_capabilities
        if capability in parent_allowed
    )
    inherited_denied = tuple(
        capability
        for capability in child_mode.allowed_capabilities
        if capability not in parent_allowed
    )
    restricted = tuple(
        dict.fromkeys(
            (
                *parent_mode.restricted_capabilities,
                *child_mode.restricted_capabilities,
                *inherited_denied,
            )
        )
    )
    return {
        "parent_mode": parent_mode,
        "child_mode": child_mode,
        "effective_allowed_capabilities": effective_allowed,
        "restricted_capabilities": restricted,
        "inherited_denied_capabilities": inherited_denied,
        "requires_scope_confirmation": (
            parent_mode.requires_scope_confirmation or child_mode.requires_scope_confirmation
        ),
    }


def get_mode_behavior_prompt(mode_key: str | None = None) -> str:
    """
    Devuelve el behavior_prompt del modo especificado.
    
    Args:
        mode_key: Clave del modo (o None para modo por defecto)
    
    Returns:
        str: Prompt de comportamiento del modo
    """
    mode = get_mode(mode_key)
    return mode.behavior_prompt


def build_mode_system_prompt(base_prompt: str, mode_key: str | None) -> str:
    mode = get_mode(mode_key)
    allowed = ", ".join(CAPABILITY_LABELS.get(cap, cap) for cap in mode.allowed_capabilities) or "ninguna"
    restricted = ", ".join(CAPABILITY_LABELS.get(cap, cap) for cap in mode.restricted_capabilities) or "ninguna"
    prompt = (
        f"{base_prompt}\n\n"
        "## MODO ACTIVO\n"
        f"Modo: {mode.name}\n"
        f"Descripcion: {mode.description}\n"
        f"Instrucciones del modo: {mode.behavior_prompt}\n"
        f"Capacidades habilitadas: {allowed}\n"
        f"Capacidades restringidas: {restricted}\n"
        f"Confirmacion de scope requerida: {'si' if mode.requires_scope_confirmation else 'no'}\n"
    )
    if mode.system_prompt:
        prompt += (
            "\n"
            "## INSTRUCCIONES ADICIONALES DEL MODO\n"
            f"{mode.system_prompt}\n"
        )
    return prompt


# ── Autonomía (iniciativa) y permisos (aprobación): dos ejes independientes ──
# Se inyectan en el system prompt (texto y voz) para que los selectores de
# configuración (agent.autonomy / agent.autonomy_level) gobiernen el comportamiento.
AUTONOMY_PROMPTS = {
    "baja": "Reactivo: ejecuta solo la tarea exacta solicitada, sin pasos extra ni iniciativa propia.",
    "media": "Equilibrado: completa la tarea solicitada incluyendo los sub-pasos razonables necesarios para lograrla.",
    "alta": "Proactivo: encadena planes de varios pasos, anticipa necesidades y propón o realiza acciones de seguimiento relacionadas.",
}
PERMISSION_PROMPTS = {
    "asistido": "pide aprobación antes de CADA acción.",
    "supervisado": "pide aprobación solo para acciones sensibles o de baja confianza.",
    "libre": "ejecuta sin pedir aprobación, salvo bloqueos directos de política.",
}

# Disciplina de observación/acción — aplica en CUALQUIER autonomía o permiso.
# Evita que el agente tome capturas o ejecute herramientas para conversar
# (p. ej. responder "¿qué puedes hacer?" no debe disparar un screenshot).
ACTION_DISCIPLINE_PROMPT = (
    "Observar (screenshot, leer pantalla) y actuar (clicks, abrir apps, terminal, navegador) "
    "son EXCLUSIVAMENTE para tareas operativas reales sobre el sistema que el usuario te pidió. "
    "Para saludos, charla, preguntas o describir QUÉ PUEDES HACER, responde solo con palabras: "
    "NUNCA tomes captura de pantalla ni ejecutes ninguna herramienta. Toma una captura únicamente "
    "como paso previo inmediato a una interacción concreta con la pantalla que el usuario solicitó."
)


def build_autonomy_context() -> str:
    """Bloque de system prompt: autonomía (iniciativa), permisos (aprobación) y disciplina de acción.

    Compartido por la ruta de texto, la voz simulada y la voz nativa (Live API) para que los
    selectores de Configuración → General se reflejen igual en todos los caminos.
    """
    autonomy = str(config.get("agent", "autonomy", default="media")).strip().lower()
    permission = str(config.get("agent", "autonomy_level", default="supervisado")).strip().lower()
    autonomy_desc = AUTONOMY_PROMPTS.get(autonomy, AUTONOMY_PROMPTS["media"])
    permission_desc = PERMISSION_PROMPTS.get(permission, PERMISSION_PROMPTS["supervisado"])
    return (
        f"[AUTONOMÍA: {autonomy} — {autonomy_desc}]\n"
        f"[PERMISOS: {permission} — {permission_desc}]\n"
        f"[DISCIPLINA: {ACTION_DISCIPLINE_PROMPT}]"
    )
