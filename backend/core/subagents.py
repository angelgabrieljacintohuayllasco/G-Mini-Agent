"""
Minimal sub-agent orchestrator for Phase 4.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable
from uuid import uuid4

from loguru import logger

from backend.core.cost_tracker import BudgetLimitExceeded, get_cost_tracker
from backend.core.modes import CAPABILITY_LABELS, get_mode, resolve_mode_capability_scope
from backend.core.prompt_manager import render_prompt_text
from backend.providers.base import LLMMessage
from backend.providers.router import ModelRouter


UpdateCallback = Callable[[dict[str, Any]], Awaitable[None]]

DEFAULT_SUBAGENT_SYSTEM_PROMPT = """Eres un sub-agente especializado de G-Mini Agent.
Tu trabajo es resolver una subtarea concreta, devolver resultados claros y no ejecutar acciones de UI.

Modo del agente principal: {parent_mode_name}
Modo del worker: {worker_mode_name}
Objetivo del worker: resolver una sola subtarea y reportar resultado util al agente principal.

Limites del worker:
- Capacidades efectivas heredadas: {effective_capabilities}
- Capacidades restringidas: {restricted_capabilities}
- Capacidades denegadas por herencia: {inherited_denied_capabilities}
- Confirmacion de scope requerida: {requires_scope_confirmation}
- Nunca asumas que tienes mas permisos que el agente principal.
- Si la subtarea requeriria una capacidad restringida, indicalo como bloqueo o riesgo en vez de inventar que puedes hacerlo.
- No ejecutes acciones de UI, terminal, navegador ni archivos; solo analiza y propone.

Formato de salida:
1. Hallazgos
2. Riesgos o vacios
3. Siguiente recomendacion
4. Resumen ejecutivo de una linea"""

DEFAULT_EXECUTOR_SYSTEM_PROMPT = """Eres un sub-agente EJECUTOR de G-Mini Agent.
Tu trabajo es completar una tarea concreta ejecutando acciones reales en el sistema.

Modelo: {model_name} | Provider: {provider_name}
Modo principal: {parent_mode_name} | Modo worker: {worker_mode_name}
Iteraciones máximas: {max_iterations}

Capacidades efectivas: {effective_capabilities}
Capacidades restringidas: {restricted_capabilities}

HERRAMIENTAS DISPONIBLES (usa formato [ACTION:tipo(params)]):

Archivos:
- [ACTION:file_read(path=ruta/archivo.py)] — Leer archivo
- [ACTION:file_write(path=ruta/archivo.py, content=...)] — Crear/sobrescribir archivo
- [ACTION:file_replace(path=ruta/archivo.py, old=texto_viejo, new=texto_nuevo)] — Reemplazar texto en archivo
- [ACTION:file_append(path=ruta/archivo.py, content=...)] — Agregar al final del archivo
- [ACTION:file_delete(path=ruta/archivo.py)] — Eliminar archivo
- [ACTION:dir_list(path=ruta/)] — Listar directorio
- [ACTION:dir_create(path=ruta/nueva/)] — Crear directorio

Terminal:
- [ACTION:terminal_run(command=...)] — Ejecutar comando en terminal
- [ACTION:terminal_run(command=..., cwd=ruta/)] — Ejecutar en directorio específico

Navegador (si disponible):
- [ACTION:browser_navigate(url=...)] — Navegar a URL
- [ACTION:browser_screenshot()] — Captura de pantalla del navegador
- [ACTION:browser_click(selector=...)] — Click en elemento
- [ACTION:browser_type(selector=..., text=...)] — Escribir en campo

MCP Tools (si disponible):
- [ACTION:mcp_call_tool(server=nombre_servidor, tool=nombre_herramienta, arguments={{...}})] — Invocar herramienta MCP

Finalización:
- [ACTION:task_complete(summary=descripción de lo completado)] — OBLIGATORIO al terminar

REGLAS:
1. Ejecuta acciones paso a paso. Cada respuesta puede contener múltiples acciones.
2. Después de ejecutar acciones, recibirás los resultados. Úsalos para decidir la siguiente acción.
3. Si una acción falla, intenta una alternativa o reporta el error.
4. SIEMPRE termina con [ACTION:task_complete(summary=...)] cuando hayas completado la tarea.
5. No inventes que hiciste algo — ejecuta la acción y espera el resultado.
6. Si necesitas leer código antes de modificarlo, usa file_read primero."""

DEFAULT_CRITIC_SYSTEM_PROMPT = """Eres el Critic Agent de G-Mini Agent.
Revisas planes de acciones sensibles antes de ejecutarse.
Debes validar coherencia del objetivo, riesgo, consistencia del plan, evidencia disponible y necesidad de handoff humano.

Modo principal: {parent_mode_name}
Modo del critic: {worker_mode_name}
Capacidades efectivas heredadas: {effective_capabilities}
Capacidades restringidas: {restricted_capabilities}

IMPORTANTE: Tu respuesta debe ser EXCLUSIVAMENTE un objeto JSON valido. No incluyas texto antes ni despues del JSON. No uses markdown. No expliques nada fuera del JSON.

Formato obligatorio:
{{"decision":"allow","confidence":0.95,"summary":"descripcion breve","findings":[{{"action":"nombre_accion","severity":"low","reason":"motivo"}}]}}

Valores para decision:
- "allow": plan coherente y seguro, ejecutar directamente
- "approve": plan aceptable pero requiere validacion humana
- "dry_run": falta evidencia o riesgo medio, simular primero
- "deny": plan inconsistente, riesgoso o incorrecto

Reglas:
- Para acciones de solo lectura (screenshots, snapshots, consultas DOM), usa "allow" con confidence >= 0.9
- Para navegacion a sitios publicos conocidos (youtube, google, etc.), usa "allow" con confidence >= 0.85
- Usa "deny" solo si el plan es claramente destructivo o malicioso
- La confidence debe ser 0-1
- No inventes capacidades que el modo no tiene
- Responde UNICAMENTE con el JSON, nada mas"""


@dataclass
class SubAgentRecord:
    id: str
    task: str
    mode: str
    name: str
    parent_mode: str
    group_id: str | None = None
    group_name: str | None = None
    session_id: str = ""
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    result: str = ""
    error: str = ""
    model: str | None = None
    provider: str | None = None
    effective_capabilities: tuple[str, ...] = ()
    restricted_capabilities: tuple[str, ...] = ()
    inherited_denied_capabilities: tuple[str, ...] = ()
    requires_scope_confirmation: bool = False
    parent_task_limit_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    budget_state: str = "unlimited"
    budget_alerts: list[str] = field(default_factory=list)
    # --- Phase 1: Executor sub-agents ---
    model_override: str | None = None
    provider_override: str | None = None
    can_execute: bool = False
    execution_results: list[dict] = field(default_factory=list)
    max_iterations: int = 10
    iterations_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        max_preview = 500 if self.can_execute else 180
        preview = self.result.strip().replace("\n", " ")
        if len(preview) > max_preview:
            preview = preview[:max_preview - 3] + "..."
        return {
            "id": self.id,
            "task": self.task,
            "mode": self.mode,
            "name": self.name,
            "parent_mode": self.parent_mode,
            "group_id": self.group_id,
            "group_name": self.group_name,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result_preview": preview,
            "error": self.error,
            "model": self.model,
            "provider": self.provider,
            "session_id": self.session_id,
            "effective_capabilities": list(self.effective_capabilities),
            "effective_capability_labels": [
                CAPABILITY_LABELS.get(capability, capability)
                for capability in self.effective_capabilities
            ],
            "restricted_capabilities": list(self.restricted_capabilities),
            "restricted_capability_labels": [
                CAPABILITY_LABELS.get(capability, capability)
                for capability in self.restricted_capabilities
            ],
            "inherited_denied_capabilities": list(self.inherited_denied_capabilities),
            "inherited_denied_labels": [
                CAPABILITY_LABELS.get(capability, capability)
                for capability in self.inherited_denied_capabilities
            ],
            "requires_scope_confirmation": self.requires_scope_confirmation,
            "parent_task_limit_usd": self.parent_task_limit_usd,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "budget_state": self.budget_state,
            "budget_alerts": list(self.budget_alerts),
            "model_override": self.model_override,
            "provider_override": self.provider_override,
            "can_execute": self.can_execute,
            "execution_results": self.execution_results[-10:] if self.execution_results else [],
            "max_iterations": self.max_iterations,
            "iterations_used": self.iterations_used,
        }


class SubAgentOrchestrator:
    def __init__(self, max_active: int = 5):
        self._max_active = max_active
        self._records: dict[str, SubAgentRecord] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._groups: dict[str, dict[str, Any]] = {}
        self._cost_tracker = get_cost_tracker()
        self._policy = None

    def _gate_actions(self, actions: list, mode_key: str) -> tuple[list, list[dict[str, Any]]]:
        """Misma policy que el coordinador. Un sub-agente no puede pedir aprobación:
        lo bloqueado o sensible no se ejecuta y vuelve como pendiente al coordinador."""
        if self._policy is None:
            from backend.core.policy import PolicyEngine

            self._policy = PolicyEngine()
        review = self._policy.review_actions(actions, mode_key=mode_key)
        if not review.get("blocked") and not review.get("requires_approval"):
            return list(actions), []
        reason = (
            "bloqueada por la política del modo activo"
            if review.get("blocked")
            else "necesita aprobación del usuario y un sub-agente no puede pedirla; "
                 "descríbela en tu resultado para que el coordinador la solicite"
        )
        return [], [
            {"action": action.type, "success": False, "message": f"No ejecutada: {reason}"} for action in actions
        ]

    def list_agents(self) -> list[dict[str, Any]]:
        items = sorted(self._records.values(), key=lambda item: item.created_at, reverse=True)
        return [item.to_dict() for item in items]

    def active_count(self) -> int:
        return sum(1 for item in self._records.values() if item.status in {"queued", "running"})

    def get_group_items(self, group_id: str) -> list[dict[str, Any]]:
        group = self._groups.get(group_id, {})
        ids = group.get("agent_ids", [])
        return [self._records[agent_id].to_dict() for agent_id in ids if agent_id in self._records]

    def is_group_finished(self, group_id: str) -> bool:
        items = self.get_group_items(group_id)
        return bool(items) and all(item["status"] in {"completed", "failed"} for item in items)

    async def spawn(
        self,
        *,
        router: ModelRouter,
        task: str,
        mode_key: str,
        parent_mode_key: str,
        session_id: str,
        parent_task_limit_usd: float = 0.0,
        context_excerpt: str = "",
        name: str | None = None,
        group_id: str | None = None,
        group_name: str | None = None,
        on_update: UpdateCallback | None = None,
        model_override: str | None = None,
        provider_override: str | None = None,
        can_execute: bool = False,
        max_iterations: int = 10,
        planner: Any | None = None,
    ) -> dict[str, Any]:
        if self.active_count() >= self._max_active:
            raise RuntimeError(f"Limite de sub-agentes alcanzado ({self._max_active})")

        scope = resolve_mode_capability_scope(parent_mode_key, mode_key)
        mode = scope["child_mode"]
        parent_mode = scope["parent_mode"]
        record = SubAgentRecord(
            id=f"sub_{uuid4().hex[:8]}",
            task=task.strip(),
            mode=mode.key,
            name=name or f"{mode.name} Worker",
            parent_mode=parent_mode.key,
            group_id=group_id,
            group_name=group_name,
            session_id=session_id,
            effective_capabilities=tuple(scope["effective_allowed_capabilities"]),
            restricted_capabilities=tuple(scope["restricted_capabilities"]),
            inherited_denied_capabilities=tuple(scope["inherited_denied_capabilities"]),
            requires_scope_confirmation=bool(scope["requires_scope_confirmation"]),
            parent_task_limit_usd=max(0.0, float(parent_task_limit_usd or 0.0)),
            model_override=model_override,
            provider_override=provider_override,
            can_execute=can_execute,
            max_iterations=max_iterations,
        )
        self._records[record.id] = record
        if group_id:
            self._groups.setdefault(group_id, {"name": group_name or "Lote", "agent_ids": []})
            self._groups[group_id]["agent_ids"].append(record.id)
        await self._notify(on_update, record)

        self._tasks[record.id] = asyncio.create_task(
            self._run_worker(
                record=record,
                router=router,
                mode_key=mode.key,
                parent_mode_key=parent_mode.key,
                context_excerpt=context_excerpt,
                on_update=on_update,
                planner=planner,
            )
        )
        return record.to_dict()

    async def spawn_batch(
        self,
        *,
        router: ModelRouter,
        subtasks: list[dict[str, str]],
        default_mode_key: str,
        parent_mode_key: str,
        session_id: str,
        parent_task_limit_usd: float = 0.0,
        context_excerpt: str = "",
        group_name: str = "Delegacion automatica",
        on_update: UpdateCallback | None = None,
        planner: Any | None = None,
    ) -> dict[str, Any]:
        group_id = f"grp_{uuid4().hex[:8]}"
        spawned: list[dict[str, Any]] = []
        for subtask in subtasks:
            item = await self.spawn(
                router=router,
                task=subtask.get("task", "").strip(),
                mode_key=subtask.get("mode", default_mode_key),
                parent_mode_key=parent_mode_key,
                session_id=session_id,
                parent_task_limit_usd=parent_task_limit_usd,
                context_excerpt=context_excerpt,
                name=subtask.get("title") or subtask.get("name"),
                group_id=group_id,
                group_name=group_name,
                on_update=on_update,
                model_override=subtask.get("model_override"),
                provider_override=subtask.get("provider_override"),
                can_execute=bool(subtask.get("can_execute", False)),
                max_iterations=int(subtask.get("max_iterations", 10)),
                planner=planner,
            )
            spawned.append(item)
        return {
            "group_id": group_id,
            "group_name": group_name,
            "items": spawned,
        }

    async def review_sensitive_actions(
        self,
        *,
        router: ModelRouter,
        actions: list[dict[str, Any]],
        mode_key: str,
        parent_mode_key: str,
        session_id: str,
        parent_task_limit_usd: float = 0.0,
        local_review: dict[str, Any],
        context_excerpt: str = "",
    ) -> dict[str, Any]:
        messages = self._build_critic_messages(
            actions=actions,
            mode_key=mode_key,
            parent_mode_key=parent_mode_key,
            local_review=local_review,
            context_excerpt=context_excerpt,
        )
        response = await router.generate_complete(
            messages=messages,
            model=router.get_current_model(),
            provider_name=router.get_current_provider_name(),
            temperature=0.05,
            max_tokens=1200,
        )
        usage_event = await self._cost_tracker.record_llm_usage(
            session_id=session_id,
            provider=response.provider or router.get_current_provider_name(),
            model=response.model or router.get_current_model(),
            source="critic_review",
            mode_key=mode_key,
            worker_id=f"critic_{uuid4().hex[:8]}",
            worker_kind="critic",
            parent_worker_id="main",
            parent_task_limit_usd=max(0.0, float(parent_task_limit_usd or 0.0)),
            input_tokens=int(response.input_tokens or 0),
            output_tokens=int(response.output_tokens or 0),
            estimated=False,
            metadata={"action_count": len(actions)},
        )
        budget_status = usage_event.get("budget_status", {})
        if isinstance(budget_status, dict) and budget_status.get("stop_required"):
            raise BudgetLimitExceeded("\n".join(budget_status.get("alerts", [])) or "Presupuesto critic excedido.")
        return self._parse_critic_json(response.text, local_review=local_review)

    async def _run_worker(
        self,
        *,
        record: SubAgentRecord,
        router: ModelRouter,
        mode_key: str,
        parent_mode_key: str,
        context_excerpt: str,
        on_update: UpdateCallback | None,
        planner: Any | None = None,
    ) -> None:
        record.status = "running"
        record.started_at = datetime.now().isoformat()
        record.model = record.model_override or router.get_current_model()
        record.provider = record.provider_override or router.get_current_provider_name()
        await self._notify(on_update, record)

        try:
            messages = self._build_messages(
                record=record,
                mode_key=mode_key,
                parent_mode_key=parent_mode_key,
                context_excerpt=context_excerpt,
            )

            max_iters = record.max_iterations if record.can_execute else 1
            max_tokens = 4096 if record.can_execute else 1400

            for iteration in range(max_iters):
                record.iterations_used = iteration + 1
                response = await router.generate_complete(
                    messages=messages,
                    model=record.model,
                    provider_name=record.provider,
                    temperature=0.3 if record.can_execute else 0.4,
                    max_tokens=max_tokens,
                )

                # -- Track usage --
                usage_event = await self._cost_tracker.record_llm_usage(
                    session_id=record.session_id,
                    provider=response.provider or record.provider or router.get_current_provider_name(),
                    model=response.model or record.model or router.get_current_model(),
                    source="subagent_executor" if record.can_execute else "subagent_worker",
                    mode_key=record.mode,
                    worker_id=record.id,
                    worker_kind="executor" if record.can_execute else "subagent",
                    parent_worker_id="main",
                    parent_task_limit_usd=record.parent_task_limit_usd,
                    input_tokens=int(response.input_tokens or 0),
                    output_tokens=int(response.output_tokens or 0),
                    estimated=False,
                    metadata={
                        "subagent_name": record.name,
                        "group_id": record.group_id,
                        "group_name": record.group_name,
                        "iteration": iteration + 1,
                        "can_execute": record.can_execute,
                    },
                )
                record.input_tokens += int(usage_event.get("input_tokens", 0) or 0)
                record.output_tokens += int(usage_event.get("output_tokens", 0) or 0)
                record.total_tokens = record.input_tokens + record.output_tokens
                record.total_cost_usd += float(usage_event.get("total_cost_usd", 0.0) or 0.0)

                budget_status = usage_event.get("budget_status", {})
                if isinstance(budget_status, dict):
                    worker_budget = budget_status.get("current_worker", {})
                    if isinstance(worker_budget, dict):
                        record.budget_state = str(worker_budget.get("state", "unlimited"))
                    record.budget_alerts = [
                        str(item) for item in budget_status.get("alerts", []) if str(item).strip()
                    ]
                    if budget_status.get("stop_required"):
                        raise BudgetLimitExceeded(
                            "\n".join(record.budget_alerts) or "Presupuesto del sub-agente excedido."
                        )

                llm_text = response.text.strip()

                # -- Non-executor: single-shot, return result --
                if not record.can_execute or not planner:
                    record.result = llm_text
                    break

                # -- Executor: check for task_complete --
                if "[ACTION:task_complete" in llm_text or "[TASK_COMPLETE]" in llm_text:
                    record.result = llm_text
                    break

                # -- Executor: parse and execute actions --
                actions = planner.parse_actions(llm_text)
                if not actions:
                    # No actions found — treat as final answer
                    record.result = llm_text
                    break

                logger.info(
                    f"Sub-agente {record.id} iteración {iteration + 1}: "
                    f"{len(actions)} acciones detectadas"
                )

                # Check for task_complete in parsed actions
                has_task_complete = any(a.type == "task_complete" for a in actions)

                # Execute actions (solo las que la policy permite sin aprobación)
                allowed, refused = self._gate_actions(actions, record.mode)
                action_results = (await planner.execute_actions(allowed) if allowed else []) + refused
                record.execution_results.extend(action_results)

                # Build execution feedback for next iteration
                feedback_parts = []
                for res in action_results:
                    status = "OK" if res.get("success") else "ERROR"
                    action_name = res.get("action", "unknown")
                    msg = str(res.get("message", res.get("data", "")))[:300]
                    feedback_parts.append(f"[{status}] {action_name}: {msg}")
                feedback_text = "\n".join(feedback_parts)

                if has_task_complete:
                    record.result = llm_text + "\n\n--- Resultados de ejecución ---\n" + feedback_text
                    break

                # Re-inject action results as assistant + user messages for next iteration
                messages.append(LLMMessage(role="assistant", content=llm_text))
                messages.append(LLMMessage(
                    role="user",
                    content=(
                        f"Resultados de las acciones ejecutadas (iteración {iteration + 1}):\n"
                        f"{feedback_text}\n\n"
                        "Continúa con la siguiente acción necesaria para completar la tarea. "
                        "Cuando termines, usa [ACTION:task_complete(summary=...)]."
                    ),
                ))
                await self._notify(on_update, record)
            else:
                # max_iterations reached without task_complete
                if not record.result:
                    record.result = (
                        f"Sub-agente alcanzó el límite de {max_iters} iteraciones sin completar la tarea.\n"
                        f"Última respuesta del LLM disponible en execution_results."
                    )

            record.status = "completed"
            record.finished_at = datetime.now().isoformat()
            await self._notify(on_update, record)
        except Exception as exc:
            logger.error(f"Sub-agente {record.id} fallo: {exc}")
            record.status = "failed"
            record.error = str(exc)
            record.finished_at = datetime.now().isoformat()
            await self._notify(on_update, record)
        finally:
            self._tasks.pop(record.id, None)

    def _build_messages(
        self,
        *,
        record: SubAgentRecord,
        mode_key: str,
        parent_mode_key: str,
        context_excerpt: str,
    ) -> list[LLMMessage]:
        mode = get_mode(mode_key)
        parent_mode = get_mode(parent_mode_key)
        effective = ", ".join(
            CAPABILITY_LABELS.get(capability, capability)
            for capability in record.effective_capabilities
        ) or "ninguna"
        restricted = ", ".join(
            CAPABILITY_LABELS.get(capability, capability)
            for capability in record.restricted_capabilities
        ) or "ninguna"
        inherited_denied = ", ".join(
            CAPABILITY_LABELS.get(capability, capability)
            for capability in record.inherited_denied_capabilities
        ) or "ninguna"

        # Choose prompt template based on executor capability
        prompt_key = "subagent_executor_system" if record.can_execute else "subagent_worker_system"
        fallback = DEFAULT_EXECUTOR_SYSTEM_PROMPT if record.can_execute else DEFAULT_SUBAGENT_SYSTEM_PROMPT

        prompt = render_prompt_text(
            prompt_key,
            fallback=fallback,
            variables={
                "parent_mode_name": parent_mode.name,
                "worker_mode_name": mode.name,
                "effective_capabilities": effective,
                "restricted_capabilities": restricted,
                "inherited_denied_capabilities": inherited_denied,
                "requires_scope_confirmation": "si" if record.requires_scope_confirmation else "no",
                "model_name": record.model or "default",
                "provider_name": record.provider or "default",
                "max_iterations": str(record.max_iterations),
            },
        )
        user_content = render_prompt_text(
            "subagent_worker_user",
            fallback="{task}\n\nContexto del agente principal:\n{context_excerpt}",
            variables={
                "task": record.task.strip(),
                "context_excerpt": context_excerpt.strip() or "Sin contexto adicional.",
            },
        )
        return [
            LLMMessage(role="system", content=prompt),
            LLMMessage(role="user", content=user_content),
        ]

    async def _notify(self, on_update: UpdateCallback | None, record: SubAgentRecord) -> None:
        if on_update is None:
            return
        await on_update(record.to_dict())

    def _build_critic_messages(
        self,
        *,
        actions: list[dict[str, Any]],
        mode_key: str,
        parent_mode_key: str,
        local_review: dict[str, Any],
        context_excerpt: str,
    ) -> list[LLMMessage]:
        scope = resolve_mode_capability_scope(parent_mode_key, mode_key)
        worker_mode = scope["child_mode"]
        parent_mode = scope["parent_mode"]
        effective = ", ".join(
            CAPABILITY_LABELS.get(capability, capability)
            for capability in scope["effective_allowed_capabilities"]
        ) or "ninguna"
        restricted = ", ".join(
            CAPABILITY_LABELS.get(capability, capability)
            for capability in scope["restricted_capabilities"]
        ) or "ninguna"
        findings_json = json.dumps(local_review.get("findings", []), ensure_ascii=False, indent=2)
        actions_json = json.dumps(actions, ensure_ascii=False, indent=2)
        prompt = render_prompt_text(
            "critic_system",
            fallback=DEFAULT_CRITIC_SYSTEM_PROMPT,
            variables={
                "parent_mode_name": parent_mode.name,
                "worker_mode_name": worker_mode.name,
                "effective_capabilities": effective,
                "restricted_capabilities": restricted,
            },
        )
        user_content = render_prompt_text(
            "critic_user",
            fallback=(
                "Solicitud para revisar:\n{context_excerpt}\n\n"
                "Acciones propuestas:\n{actions_json}\n\n"
                "Revisión local previa:\n{findings_json}\n"
            ),
            variables={
                "context_excerpt": context_excerpt.strip() or "Sin contexto adicional.",
                "actions_json": actions_json,
                "findings_json": findings_json,
            },
        )
        return [
            LLMMessage(role="system", content=prompt),
            LLMMessage(role="user", content=user_content),
        ]

    def _extract_json_payload(self, raw_text: str) -> dict[str, Any] | None:
        text = raw_text.strip()
        if not text:
            return None

        code_block = re.search(r"```json\s*([\s\S]*?)```", text, re.IGNORECASE)
        if code_block:
            text = code_block.group(1).strip()

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", text):
            try:
                candidate, _ = decoder.raw_decode(text[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                return candidate
        return None

    def _parse_critic_json(self, raw_text: str, *, local_review: dict[str, Any]) -> dict[str, Any]:
        data = self._extract_json_payload(raw_text)
        if data is None:
            return {
                "decision": "approve",
                "confidence": 0.0,
                "summary": "El critic no devolvio JSON valido; se requiere validacion humana.",
                "findings": [
                    {
                        "action": "critic_review",
                        "severity": "high",
                        "confidence": 0.0,
                        "threshold": 0.0,
                        "reason": "respuesta invalida del critic",
                    }
                ],
            }

        decision = str(data.get("decision", "approve")).strip().lower()
        decision_aliases = {
            "block": "deny",
            "reject": "deny",
            "review": "approve",
            "human_review": "approve",
            "simulate": "dry_run",
            "simulation": "dry_run",
            "dryrun": "dry_run",
            "execute": "allow",
        }
        decision = decision_aliases.get(decision, decision)
        if decision not in {"allow", "approve", "dry_run", "deny"}:
            decision = "approve"

        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        local_findings_by_action: dict[str, dict[str, Any]] = {}
        for item in local_review.get("findings", []):
            if not isinstance(item, dict):
                continue
            action_name = str(item.get("action", "")).strip()
            if action_name and action_name not in local_findings_by_action:
                local_findings_by_action[action_name] = item

        findings: list[dict[str, Any]] = []
        for item in data.get("findings", []):
            if not isinstance(item, dict):
                continue
            action_name = str(item.get("action", "critic_review")).strip() or "critic_review"
            enriched = {
                "action": action_name,
                "severity": str(item.get("severity", "medium")).strip().lower() or "medium",
                "reason": str(item.get("reason", "")).strip() or "sin detalle",
            }
            local_item = local_findings_by_action.get(action_name)
            if local_item:
                for key in (
                    "confidence",
                    "threshold",
                    "capability",
                    "capability_label",
                    "category",
                    "autonomy_level",
                    "mode",
                    "mode_name",
                ):
                    if key in local_item:
                        enriched[key] = local_item[key]
            findings.append(enriched)

        if not findings and local_review.get("findings"):
            findings = [
                {
                    "action": item.get("action", "critic_review"),
                    "severity": item.get("severity", "medium"),
                    "confidence": item.get("confidence", 0.0),
                    "threshold": item.get("threshold", 0.0),
                    "capability": item.get("capability"),
                    "capability_label": item.get("capability_label"),
                    "category": item.get("category"),
                    "reason": item.get("reason", "sin detalle"),
                }
                for item in local_review.get("findings", [])
            ]

        return {
            "decision": decision,
            "confidence": confidence,
            "summary": str(data.get("summary", "")).strip() or "Sin resumen del critic.",
            "findings": findings,
        }
