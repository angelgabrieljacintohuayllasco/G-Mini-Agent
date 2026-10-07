"""
G-Mini Agent — Comandos "/" del chat.

Funcionan igual en la app, la CLI, Telegram y la API. Los incluidos resuelven
cosas sin pasar por el modelo (o le dan una instrucción precisa); los propios
son archivos data/commands/<nombre>.md cuyo contenido es una plantilla de
prompt: $ARGUMENTS se reemplaza por lo que se escribió después del comando.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from backend.config import ROOT_DIR

COMMANDS_DIR = ROOT_DIR / "data" / "commands"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
MAX_TEMPLATE_BYTES = 64 * 1024


@dataclass
class SlashResult:
    reply: str | None = None     # respuesta directa, sin llamar al modelo
    prompt: str | None = None    # texto que recibe el modelo en lugar del comando
    plan_mode: bool = False      # el modelo propone un plan y no ejecuta acciones


Handler = Callable[[Any, str], Awaitable[SlashResult]]
_BUILTINS: dict[str, tuple[Handler, str, str]] = {}
_ALIASES = {"help": "ayuda", "new": "nuevo", "mode": "modo", "memory": "recuerdos", "forget": "olvidar",
            "name": "nombre", "cost": "costos", "costs": "costos", "stop": "detener", "run": "ejecutar"}


def builtin(name: str, usage: str, help_text: str):
    def register(fn: Handler) -> Handler:
        _BUILTINS[name] = (fn, usage, help_text)
        return fn
    return register


def _custom_commands() -> dict[str, Path]:
    if not COMMANDS_DIR.is_dir():
        return {}
    found = {}
    for path in sorted(COMMANDS_DIR.glob("*.md")):
        name = path.stem.lower()
        if _NAME_RE.match(name) and name not in _BUILTINS:
            found[name] = path
    return found


def _describe_custom(path: Path) -> str:
    try:
        first = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[0]
    except (OSError, IndexError):
        return ""
    return first.lstrip("#").strip()[:100]


async def handle(agent: Any, text: str) -> SlashResult | None:
    """None si el texto no es un comando; si lo es, qué hacer con él."""
    stripped = (text or "").strip()
    if not stripped.startswith("/") or stripped.startswith("//") or len(stripped) < 2:
        return None
    head, _, args = stripped[1:].partition(" ")
    name = _ALIASES.get(head.lower(), head.lower())
    args = args.strip()
    if name in _BUILTINS:
        return await _BUILTINS[name][0](agent, args)
    custom = _custom_commands().get(name)
    if custom is not None:
        template = custom.read_bytes()[:MAX_TEMPLATE_BYTES].decode("utf-8", errors="replace")
        prompt = template.replace("$ARGUMENTS", args) if "$ARGUMENTS" in template else f"{template}\n\n{args}".strip()
        return SlashResult(prompt=prompt)
    if not _NAME_RE.match(name):
        return None  # "/ruta/de/archivo" u otra cosa que no es un comando
    return SlashResult(reply=f"No conozco el comando /{name}. Escribe /ayuda para ver la lista.")


# ── Incluidos ─────────────────────────────────────────────────────────────

@builtin("ayuda", "/ayuda", "Lista los comandos disponibles.")
async def _help(agent: Any, args: str) -> SlashResult:
    lines = ["Comandos:"]
    lines += [f"- {usage}: {text}" for _fn, usage, text in _BUILTINS.values()]
    custom = _custom_commands()
    if custom:
        lines.append("\nTus comandos (data/commands):")
        lines += [f"- /{name}: {_describe_custom(path)}" for name, path in custom.items()]
    return SlashResult(reply="\n".join(lines))


@builtin("nuevo", "/nuevo", "Empieza una conversación nueva.")
async def _new(agent: Any, args: str) -> SlashResult:
    await agent.new_session()
    return SlashResult(reply="Listo, empezamos una conversación nueva.")


@builtin("modo", "/modo [nombre]", "Muestra los modos o cambia al indicado.")
async def _mode(agent: Any, args: str) -> SlashResult:
    from backend.core.modes import list_modes

    modes = {m["key"]: m for m in list_modes()}
    if not args:
        lines = [f"Modo actual: {agent.current_mode}. Disponibles:"]
        lines += [f"- {key}: {m.get('name', key)}" for key, m in modes.items()]
        return SlashResult(reply="\n".join(lines))
    key = args.split()[0].lower()
    if key not in modes:
        return SlashResult(reply=f"No existe el modo '{key}'. Escribe /modo para ver la lista.")
    info = agent.set_mode(key)
    return SlashResult(reply=f"Modo cambiado a {info.get('current_mode_name', key)}.")


@builtin("plan", "/plan <tarea>", "Propone un plan paso a paso sin ejecutar nada.")
async def _plan(agent: Any, args: str) -> SlashResult:
    if not args:
        return SlashResult(reply="Escribe la tarea después de /plan.")
    return SlashResult(prompt=args, plan_mode=True)


@builtin("ejecutar", "/ejecutar", "Ejecuta el último plan propuesto.")
async def _run_plan(agent: Any, args: str) -> SlashResult:
    extra = f" Ajuste del usuario: {args}" if args else ""
    return SlashResult(prompt=f"Ejecuta el plan que propusiste arriba, paso a paso, verificando cada resultado.{extra}")


@builtin("skills", "/skills", "Lista las skills de instrucciones disponibles.")
async def _skills(agent: Any, args: str) -> SlashResult:
    import asyncio

    from backend.core import agent_skills

    skills = [s for s in await asyncio.to_thread(agent_skills.discover) if s.enabled and s.lifecycle != "archived"]
    if not skills:
        return SlashResult(reply="No hay skills instaladas.")
    return SlashResult(reply="Skills:\n" + "\n".join(f"- {s.name}: {s.description[:110]}" for s in skills))


@builtin("skill", "/skill <nombre> [tarea]", "Hace la tarea siguiendo una skill concreta.")
async def _skill(agent: Any, args: str) -> SlashResult:
    name, _, task = args.partition(" ")
    if not name:
        return SlashResult(reply="Indica la skill: /skill <nombre> [tarea]. Usa /skills para verlas.")
    return SlashResult(prompt=(
        f'Carga las instrucciones de la skill "{name}" con skill_read y síguelas para esta tarea: '
        f"{task.strip() or 'pregúntame qué necesito'}"
    ))


@builtin("recuerdos", "/recuerdos [tema]", "Muestra lo que recuerdas (todo o sobre un tema).")
async def _memories(agent: Any, args: str) -> SlashResult:
    import asyncio

    from backend.core.memory_ltm import get_ltm

    ltm = get_ltm()
    if args:
        items = await asyncio.to_thread(ltm.search, args, 8, None, None, touch=False)
        rows = [f"- [{h['memory_id']}] {h['content']}" for h in items]
    else:
        items = await asyncio.to_thread(ltm.list_memories, None, 15, 0)
        rows = [f"- [{m['memory_id']}] {m['content']}" for m in items]
    if not rows:
        return SlashResult(reply="No recuerdo nada" + (f" sobre '{args}'." if args else " todavía."))
    return SlashResult(reply="Lo que recuerdo:\n" + "\n".join(rows) + "\n\nPara borrar uno: /olvidar <id>")


@builtin("olvidar", "/olvidar <id>", "Borra un recuerdo por su id (míralo con /recuerdos).")
async def _forget(agent: Any, args: str) -> SlashResult:
    import asyncio

    from backend.core.memory_ltm import get_ltm

    memory_id = args.split()[0] if args else ""
    if not memory_id:
        return SlashResult(reply="Indica el id del recuerdo: /olvidar <id>. Lo ves con /recuerdos.")
    ltm = get_ltm()
    row = await asyncio.to_thread(ltm.get, memory_id)
    if row is None:
        return SlashResult(reply=f"No encontré el recuerdo {memory_id}.")
    await asyncio.to_thread(ltm.delete, memory_id)
    agent._apply_system_prompt()
    return SlashResult(reply=f"Olvidado: {row['content']}")


@builtin("nombre", "/nombre <nuevo>", "Cambia el nombre del agente.")
async def _name(agent: Any, args: str) -> SlashResult:
    from backend.core.identity import set_agent_name

    try:
        name = set_agent_name(args)
    except ValueError as exc:
        return SlashResult(reply=str(exc))
    agent._apply_system_prompt()
    return SlashResult(reply=f"Desde ahora me llamo {name}.")


@builtin("costos", "/costos", "Gasto de hoy y del mes en modelos de IA.")
async def _costs(agent: Any, args: str) -> SlashResult:
    from backend.core.cost_tracker import get_cost_tracker

    try:
        summary = await get_cost_tracker().get_summary()
    except Exception as exc:
        logger.debug(f"/costos: {exc}")
        return SlashResult(reply="No pude leer el registro de costos.")
    today, month = summary.get("today") or {}, summary.get("month") or {}
    return SlashResult(reply=(
        f"Hoy: US$ {float(today.get('total_cost_usd') or 0):.4f} en {today.get('event_count', 0)} llamadas. "
        f"Este mes: US$ {float(month.get('total_cost_usd') or 0):.4f}."
    ))


@builtin("detener", "/detener", "Detiene lo que el agente esté haciendo.")
async def _stop(agent: Any, args: str) -> SlashResult:
    await agent.stop()
    return SlashResult(reply="Detenido.")
