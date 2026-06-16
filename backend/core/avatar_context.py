"""Contexto del avatar para inyectar en los system prompts.

El agente necesita saber si el usuario configuro un avatar 3D, 2D o ninguno
(seccion `character` de la config) para no negar que tiene cuerpo/skin
cuando se le pregunta (p.ej. "¿puedes sonreir?").
"""

from __future__ import annotations

from backend.config import config


def build_avatar_context() -> str:
    """Bloque [AVATAR] segun la configuracion actual del personaje."""
    char_type = str(config.get("character", "type", default="3d") or "3d").lower()
    if char_type == "none":
        return (
            "[AVATAR]\n"
            "El usuario desactivo tu avatar visual: ahora mismo no tienes "
            "representacion grafica en pantalla (solo texto/voz). Si te "
            "preguntan por tu apariencia, explica que el avatar esta "
            "desactivado en la configuracion y puede activarse en Settings."
        )

    skin = str(config.get("character", "skin", default="energy-ball") or "energy-ball")
    mode = str(config.get("character", "mode", default="chat") or "chat")
    emotions = bool(config.get("character", "emotions_enabled", default=False))

    kind = "3D (modelo VRM/VRoid)" if char_type == "3d" else "2D (sprites animados)"
    donde = (
        "flotando sobre el escritorio del usuario"
        if mode == "skin"
        else "junto a la ventana de chat"
    )
    lines = [
        "[AVATAR]",
        f"Tienes un avatar {kind} visible {donde} (skin activa: '{skin}').",
        "Tu avatar parpadea, respira, saluda al aparecer, mueve los labios al "
        "hablar y gesticula con las manos mientras explicas.",
    ]
    if emotions:
        lines.append(
            "Ademas puede expresar emociones faciales y corporales; usa los "
            "tags de emocion descritos en [EXPRESIONES DEL AVATAR] cuando "
            "esten disponibles."
        )
    else:
        lines.append(
            "Las emociones del avatar estan desactivadas en la configuracion "
            "del usuario."
        )
    return "\n".join(lines)
