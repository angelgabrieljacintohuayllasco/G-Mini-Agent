"""
G-Mini Agent — Acciones de memoria e identidad que puede pedir el modelo.

- memory_search(query, top_k): busca en lo que recuerda del usuario.
- memory_forget(memory_id): borra un recuerdo (el usuario pidió olvidarlo).
- agent_rename(name): el usuario le pide al agente que se cambie el nombre.

No hay "memory_remember": lo que el usuario cuenta se guarda en la reflexión
de fin de turno, que solo lee lo que él escribió. Así una página web o un
archivo no pueden dejar recuerdos falsos pidiéndole al modelo que los guarde.

Las funciones son bloqueantes (SQLite, embeddings): el planner las corre en un hilo.
"""

from __future__ import annotations

from typing import Any, Callable

PROMPT_REFRESH_ACTIONS = frozenset({"memory_forget", "agent_rename"})


def _fail(message: str) -> dict[str, Any]:
    return {"success": False, "message": message}


def memory_search(params: dict[str, Any]) -> dict[str, Any]:
    from backend.core.memory_ltm import get_ltm

    query = " ".join(str(params.get("query") or "").split())
    if not query:
        return _fail("Falta query en memory_search")
    try:
        top_k = max(1, min(int(params.get("top_k", 5) or 5), 20))
    except (TypeError, ValueError):
        top_k = 5
    hits = get_ltm().search(query, top_k=top_k, touch=False)
    items = [
        {"memory_id": h["memory_id"], "category": h["category"], "text": h["content"],
         "similarity": h["similarity"]}
        for h in hits
    ]
    message = f"{len(items)} recuerdo(s) para '{query}'" if items else f"No recuerdo nada sobre '{query}'"
    return {"success": True, "message": message, "data": {"memories": items}}


def memory_forget(params: dict[str, Any]) -> dict[str, Any]:
    from backend.core.memory_ltm import get_ltm

    memory_id = str(params.get("memory_id") or "").strip()
    if not memory_id:
        return _fail("Falta memory_id: busca primero con memory_search(query=...) y usa el id del resultado")
    ltm = get_ltm()
    row = ltm.get(memory_id)
    if row is None:
        return _fail(f"No existe el recuerdo {memory_id}")
    ltm.delete(memory_id)
    return {"success": True, "message": f"Olvidado: {row['content']}", "data": {"memory_id": memory_id}}


def agent_rename(params: dict[str, Any]) -> dict[str, Any]:
    from backend.core.identity import set_agent_name

    try:
        name = set_agent_name(str(params.get("name") or ""))
    except ValueError as exc:
        return _fail(str(exc))
    return {"success": True, "message": f"Ahora me llamo {name}", "data": {"name": name}}


_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "memory_search": memory_search,
    "memory_forget": memory_forget,
    "agent_rename": agent_rename,
}


def run(action_type: str, params: dict[str, Any]) -> dict[str, Any]:
    handler = _HANDLERS.get(action_type)
    if handler is None:
        return _fail(f"Acción de memoria desconocida: {action_type}")
    try:
        return handler(params if isinstance(params, dict) else {})
    except Exception as exc:
        return _fail(f"{action_type} falló: {exc}")
