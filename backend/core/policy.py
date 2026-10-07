"""
Policy, capability gating and approval helpers for sensitive agent actions.
"""

from __future__ import annotations

from typing import Any

from backend.config import config
from backend.core.exec_approvals import evaluate_command
from backend.core.modes import CAPABILITY_LABELS, DEFAULT_MODE_KEY, get_mode
from backend.core.payment_registry import PaymentRegistry
from backend.core.planner import Action
from backend.core.skill_registry import SkillRegistry


PAYMENT_USD_KEYS = {
    "amount_usd",
    "usd_amount",
    "price_usd",
    "cost_usd",
    "total_usd",
    "max_spend_usd",
    "spend_limit_usd",
}
PAYMENT_GENERIC_KEYS = {
    "amount",
    "price",
    "cost",
    "total",
    "budget",
    "value",
    "max_spend",
    "spend_limit",
}
PAYMENT_CURRENCY_KEYS = {"currency", "currency_code", "amount_currency"}
PAYMENT_ACCOUNT_KEYS = {
    "account_id",
    "payment_account_id",
    "payment_method_id",
    "card_id",
    "wallet_id",
}
PAYMENT_TOOL_HINTS = (
    "pay",
    "payment",
    "payments",
    "charge",
    "checkout",
    "purchase",
    "buy",
    "invoice",
    "billing",
    "subscription",
    "transfer",
    "refund",
    "payout",
)
SPEND_PERMISSION_MODES = {
    "deny_all",
    "ask_always",
    "ask_above_x",
    "auto_approve_under_x",
}


APPROVE_WORDS = {"aprobar", "aprueba", "si", "sí", "confirmo", "confirmar", "dale", "ok", "okay", "continua", "continúa"}
REJECT_WORDS = {"cancelar", "cancela", "no", "rechaza", "deten", "detén", "stop"}

DEFAULT_CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "reading": 0.55,
    "interactive": 0.60,
    "files": 0.75,
    "publishing": 0.85,
    "payments": 0.95,
    "system": 0.85,
}


def _get_autonomy_level() -> str:
    raw = str(config.get("agent", "autonomy_level", default="supervisado")).strip().lower()
    if raw in {"libre", "supervisado", "asistido"}:
        return raw
    return "supervisado"


def get_confidence_thresholds() -> dict[str, float]:
    thresholds = dict(DEFAULT_CONFIDENCE_THRESHOLDS)
    raw = config.get("agent", "critic_thresholds", default={})
    if not isinstance(raw, dict):
        return thresholds

    for category, value in raw.items():
        key = str(category).strip().lower()
        if not key:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        thresholds[key] = max(0.0, min(1.0, parsed))
    return thresholds


def get_confidence_threshold(category: str) -> float:
    thresholds = get_confidence_thresholds()
    normalized = str(category).strip().lower()
    return thresholds.get(normalized, DEFAULT_CONFIDENCE_THRESHOLDS["system"])


def _coerce_non_negative_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < 0:
        return default
    return parsed


def get_spend_permissions() -> dict[str, Any]:
    enabled = bool(config.get("payments", "enabled", default=True))
    mode = str(config.get("payments", "spend_permissions_mode", default="ask_always")).strip().lower()
    if mode not in SPEND_PERMISSION_MODES:
        mode = "ask_always"
    if not enabled:
        mode = "deny_all"
    return {
        "enabled": enabled,
        "mode": mode,
        "ask_above_usd": _coerce_non_negative_float(
            config.get("payments", "ask_above_usd", default=25.0),
            25.0,
        ),
        "auto_approve_under_usd": _coerce_non_negative_float(
            config.get("payments", "auto_approve_under_usd", default=5.0),
            5.0,
        ),
    }


# Un script de navegador se considera "solo lectura" únicamente si NO contiene
# ninguno de estos marcadores. La lista es de bloqueo (lo prohibido), no de
# permiso: un script que exfiltra con `new Image().src = ... + document.cookie`
# o `navigator.sendBeacon(...)` debe clasificarse como crítico, no como lectura.
_BROWSER_EXFIL_OR_MUTATION_MARKERS = (
    ".click(", ".submit(", ".focus(", ".blur(", ".remove(", ".append(",
    ".prepend(", ".insertadjacent", ".value", ".checked", ".setattribute(",
    ".innerhtml", ".outerhtml", ".settimeout", ".setinterval",
    "location", "window.open", "document.write", "document.cookie",
    "localstorage", "sessionstorage", "indexeddb",
    "fetch(", "xmlhttprequest", "sendbeacon", "websocket", "eventsource",
    "new image", "importscripts", "import(", "navigator.",
    "=",  # cualquier asignación puede mutar o preparar una exfiltración
    "`",  # template literals: forma habitual de construir URLs de exfiltración
)
# Solo se permite como lectura si además hace una consulta/lectura del DOM;
# así un `return 1` vacío no cuenta como acceso de lectura legítimo.
_BROWSER_READ_MARKERS = (
    "queryselector", ".gettext", ".innertext", ".textcontent", ".href",
    ".getattribute(", ".dataset", ".length", ".children", ".parentelement",
    ".closest(", ".matches(", "getelementby", "getelementsby",
)


def _looks_read_only_browser_script(script: str) -> bool:
    normalized = script.strip().lower()
    if not normalized:
        return False
    if any(marker in normalized for marker in _BROWSER_EXFIL_OR_MUTATION_MARKERS):
        return False
    return any(marker in normalized for marker in _BROWSER_READ_MARKERS)


class PolicyEngine:
    def __init__(
        self,
        skill_registry: SkillRegistry | None = None,
        payment_registry: PaymentRegistry | None = None,
    ):
        self._skills = skill_registry or SkillRegistry()
        self._payments = payment_registry or PaymentRegistry()

    def review_actions(self, actions: list[Action], mode_key: str | None = None) -> dict[str, Any]:
        mode = get_mode(mode_key or DEFAULT_MODE_KEY)
        autonomy_level = _get_autonomy_level()
        thresholds = get_confidence_thresholds()
        findings: list[dict[str, Any]] = []
        blocked = False
        requires_approval = False
        requires_critic = False
        requires_dry_run = False

        for action in actions:
            review = self._classify(action)
            capability = review["capability"]
            category = review["category"]
            threshold = thresholds.get(category, DEFAULT_CONFIDENCE_THRESHOLDS["system"])
            review["threshold"] = threshold
            review["mode"] = mode.key
            review["autonomy_level"] = autonomy_level
            review["below_threshold"] = review["confidence"] < threshold

            if action.type == "terminal_run":
                exec_approval = evaluate_command(str(action.params.get("command", "")))
                review["exec_approval"] = exec_approval.to_dict()
                if exec_approval.enabled and not exec_approval.allowed:
                    blocked = True
                    review["effect"] = "deny"
                    review["reason"] = (
                        f"exec approvals bloqueó el comando en {exec_approval.host_key}: {exec_approval.reason}"
                    )
                    findings.append(review)
                    continue

            if capability and capability not in mode.allowed_capabilities:
                blocked = True
                review["effect"] = "deny"
                review["reason"] = (
                    f"el modo {mode.name} no habilita {CAPABILITY_LABELS.get(capability, capability)}"
                )
                findings.append(review)
                continue

            if mode.requires_scope_confirmation and capability == "offensive_security":
                blocked = True
                review["effect"] = "deny"
                review["reason"] = (
                    "este modo exige confirmar scope autorizado antes de cualquier accion ofensiva"
                )
                findings.append(review)
                continue

            if category == "payments":
                spend_result = self._apply_spend_policy(action, review)
                if spend_result.get("blocked"):
                    blocked = True
                    review["effect"] = "deny"
                    findings.append(review)
                    continue

            approval_override = str(review.get("approval_override", "")).strip().lower()
            critic_override = str(review.get("critic_override", "")).strip().lower()
            force_sensitive = critic_override == "force"
            is_sensitive = force_sensitive or review["severity"] in {"high", "critical"} or review["confidence"] < threshold
            if is_sensitive:
                if critic_override != "skip":
                    requires_critic = True
                    requires_dry_run = requires_dry_run or review["confidence"] < threshold
                if approval_override == "require":
                    requires_approval = True
                elif approval_override != "skip" and autonomy_level != "libre":
                    requires_approval = True
                review["effect"] = "approve"
                findings.append(review)
                continue

            if autonomy_level == "asistido":
                requires_approval = True
                review["effect"] = "approve"
                review["reason"] = (
                    f"{review['reason']}; el nivel de autonomia asistido exige aprobacion explicita"
                )
                findings.append(review)

        return {
            "mode": mode.key,
            "mode_name": mode.name,
            "autonomy_level": autonomy_level,
            "blocked": blocked,
            "requires_approval": requires_approval and not blocked,
            "requires_critic": requires_critic and not blocked,
            "requires_dry_run": requires_dry_run and not blocked,
            "highest_threshold": max(
                (float(item.get("threshold", 0.0)) for item in findings),
                default=0.0,
            ),
            "thresholds": thresholds,
            "findings": findings,
        }

    def _classify(self, action: Action) -> dict[str, Any]:
        action_type = action.type
        params = action.params

        if action_type in {
            "task_complete",
            "wait",
            "screenshot",
            "chrome_list_profiles",
            "downloads_check",
            "terminal_list",
            "browser_snapshot",
            "browser_extract",
            "browser_state",
            "file_exists",
            "file_list",
            "file_read_text",
            "file_read_batch",
            "file_search_text",
            "workspace_snapshot",
            "git_status",
            "git_changed_files",
            "git_diff",
            "git_log",
            "code_outline",
            "code_related_files",
            "ide_detect",
            "ide_state",
            "ide_active_file",
            "ide_selection",
            "ide_workspace_folders",
            "ide_diagnostics",
            "ide_symbols",
            "ide_find_symbol",
            "skills_catalog",
            "mcp_list_servers",
            "mcp_list_tools",
            "payments_list_accounts",
            "payments_get_account",
            "gateway_status",
            "gateway_list_sessions",
            "gateway_list_outbox",
            "budget_weekly_report",
            "schedule_list_jobs",
            "schedule_list_runs",
            "memory_search",
        }:
            return self._review(action, "observe", "reading", "low", 0.95, "accion de lectura o cierre")

        if action_type == "memory_forget":
            return self._review(action, "observe", "files", "medium", 0.80, "borra un recuerdo del usuario a pedido suyo")

        if action_type == "agent_rename":
            return self._review(action, "observe", "interactive", "low", 0.90, "cambia el nombre con el que se presenta el agente")

        if action_type == "gateway_notify":
            return self._review(action, "development", "publishing", "medium", 0.82, "envia una notificacion por el gateway configurado")

        if action_type == "file_write_text":
            return self._review(action, "file_scan", "files", "medium", 0.82, "escribe texto en un archivo local")

        if action_type == "file_replace_text":
            return self._review(action, "development", "files", "high", 0.72, "modifica contenido existente de un archivo local")

        if action_type in {"skill_install_local", "skill_install_git"}:
            return self._review(action, "development", "system", "high", 0.68, "instala una skill que extiende capacidades del agente")

        if action_type in {"skill_enable", "skill_disable"}:
            return self._review(action, "development", "system", "medium", 0.74, "activa o desactiva una skill del agente")

        if action_type == "skill_uninstall":
            return self._review(action, "development", "system", "high", 0.70, "desinstala una skill del agente")

        if action_type in {"skill_author", "skill_create", "skill_update"}:
            # El agente escribe código ejecutable nuevo: siempre con aprobación
            # humana, incluso en modo libre (approval_override="require").
            review = self._review(
                action, "development", "system", "high", 0.45,
                "crea o modifica una skill con código que luego puede ejecutarse",
            )
            review["approval_override"] = "require"
            return review

        if action_type == "skill_run":
            return self._review_skill_run(action)

        if action_type == "mcp_call_tool":
            return self._review_mcp_call(action)

        if action_type in {"schedule_create_job", "schedule_update_job", "schedule_delete_job"}:
            return self._review(action, "development", "system", "high", 0.68, "gestiona tareas programadas persistentes del agente")

        if action_type == "schedule_run_job":
            return self._review(action, "development", "system", "high", 0.60, "ejecuta manualmente un job programado que puede invocar skills o tools MCP")

        if action_type in {"schedule_emit_event", "schedule_emit_heartbeat", "schedule_trigger_webhook"}:
            return self._review(action, "development", "system", "high", 0.66, "dispara triggers del scheduler que pueden ejecutar skills o tools MCP")

        if action_type == "terminal_run":
            return self._review(action, "development", "system", "high", 0.55, "ejecuta comandos en una terminal del sistema")

        if action_type in {"ide_open_workspace", "ide_open_file", "ide_open_diff"}:
            return self._review(action, "development", "interactive", "medium", 0.80, "abre o enfoca recursos de desarrollo en un editor local")

        if action_type == "ide_apply_edit":
            return self._review(action, "development", "files", "high", 0.74, "aplica una edicion dirigida dentro del IDE")

        if action_type == "ide_reveal_range":
            return self._review(action, "development", "interactive", "medium", 0.78, "navega a un rango concreto dentro del IDE")

        if action_type == "ide_reveal_symbol":
            return self._review(action, "development", "interactive", "medium", 0.80, "navega a un simbolo concreto dentro del IDE")

        if action_type in {"ide_open_diagnostic", "ide_next_diagnostic", "ide_prev_diagnostic"}:
            return self._review(action, "development", "interactive", "medium", 0.79, "navega a diagnosticos del IDE")

        if action_type == "ide_apply_workspace_edits":
            return self._review(action, "development", "files", "high", 0.76, "aplica multiples ediciones dentro del IDE")

        if action_type == "browser_download_click":
            return self._review(action, "browser_download", "files", "medium", 0.85, "descarga un archivo al sistema")

        if action_type == "browser_scan_file":
            return self._review(action, "file_scan", "files", "medium", 0.90, "escanea un archivo antes de usarlo")

        if action_type == "browser_eval":
            script = str(params.get("script", ""))
            if _looks_read_only_browser_script(script):
                return self._review(action, "browser_dom", "reading", "medium", 0.78, "lee datos del DOM del navegador")
            return self._review(action, "browser_dom", "system", "critical", 0.40, "ejecuta script arbitrario dentro del navegador")

        if action_type in {"chrome_open_profile", "chrome_open_automation_profile", "browser_use_profile", "browser_use_automation_profile"}:
            return self._review(action, "browser_control", "interactive", "medium", 0.82, "abre o conecta una sesion de navegador")

        if action_type in {"browser_navigate", "browser_click", "browser_type", "browser_press"}:
            return self._review(action, "browser_control", "interactive", "medium", 0.80, "interaccion activa con navegador")

        if action_type in {"click", "double_click", "right_click", "type", "focus_type", "press", "move", "drag", "scroll"}:
            return self._review(action, "desktop_control", "interactive", "medium", 0.78, "interaccion activa con escritorio")

        if action_type == "hotkey":
            keys = str(params.get("keys", "")).lower()
            if any(token in keys for token in ["alt+f4", "win+r", "shift+delete", "ctrl+shift+esc"]):
                return self._review(action, "desktop_control", "system", "high", 0.45, f"hotkey sensible: {keys}")
            return self._review(action, "desktop_control", "interactive", "medium", 0.72, f"hotkey: {keys}")

        if action_type in {"adb_tap", "adb_swipe", "adb_text"}:
            return self._review(action, "mobile_control", "interactive", "medium", 0.75, "interaccion activa con Android")

        if action_type.startswith("browser_"):
            return self._review(action, "browser_dom", "reading", "medium", 0.88, "accion web no clasificada de forma especifica")

        return self._review(action, None, "system", "high", 0.35, "accion no catalogada; requiere revision humana")

    def _review_skill_run(self, action: Action) -> dict[str, Any]:
        skill_id = str(action.params.get("skill_id", "")).strip()
        if not skill_id:
            return self._review(action, "development", "system", "high", 0.40, "ejecuta una skill sin skill_id valido")

        try:
            skill = self._skills.get_skill(skill_id)
        except KeyError:
            return self._review(action, "development", "system", "high", 0.40, "ejecuta una skill no registrada")

        permissions = {str(item).strip().lower() for item in skill.get("requires_permissions", []) if str(item).strip()}
        if any(token in permissions for token in {"payments", "payments.execute"}):
            return self._review(action, "development", "payments", "critical", 0.35, "ejecuta una skill con permisos de pago")

        if any(token in permissions for token in {"files.write", "files.delete", "system.exec", "desktop.control"}):
            return self._review(action, "development", "system", "high", 0.58, "ejecuta una skill con permisos de escritura o control del sistema")

        if any(token in permissions for token in {"browser.control", "ide.control", "mobile.control"}):
            return self._review(action, "development", "interactive", "high", 0.64, "ejecuta una skill con control interactivo sobre herramientas del usuario")

        if any(token in permissions for token in {"files.read", "web_fetch", "browser.read"}):
            return self._review(action, "development", "reading", "medium", 0.82, "ejecuta una skill de lectura o consulta")

        return self._review(action, "development", "system", "high", 0.55, "ejecuta una skill generica en subprocesso aislado")

    def _review_mcp_call(self, action: Action) -> dict[str, Any]:
        tool_name = str(action.params.get("tool", action.params.get("name", ""))).strip().lower()
        arguments = action.params.get("arguments", action.params.get("input", {}))
        if self._looks_payment_tool(tool_name, arguments):
            return self._review(action, "development", "payments", "critical", 0.42, "invoca una tool MCP con indicios de pago o cobro")
        if tool_name.startswith(("get", "list", "read", "search", "fetch", "query")):
            return self._review(action, "observe", "reading", "medium", 0.74, "invoca una tool MCP de lectura")
        return self._review(action, "development", "system", "high", 0.52, "invoca una tool MCP externa potencialmente sensible")

    def _apply_spend_policy(self, action: Action, review: dict[str, Any]) -> dict[str, Any]:
        policy = get_spend_permissions()
        amount_info = self._extract_payment_amount(action)
        account_info = self._resolve_payment_account(action)
        amount_usd = amount_info.get("amount_usd")
        raw_amount = amount_info.get("raw_amount")
        currency = amount_info.get("currency") or ""
        review["amount_usd"] = amount_usd
        review["raw_amount"] = raw_amount
        review["payment_currency"] = currency
        review["payment_amount_source"] = amount_info.get("source")
        review["payment_amount_verified"] = bool(amount_info.get("verified_usd"))
        review["spend_policy_mode"] = policy["mode"]
        review["spend_ask_above_usd"] = policy["ask_above_usd"]
        review["spend_auto_approve_under_usd"] = policy["auto_approve_under_usd"]
        review["payment_account_requested"] = account_info.get("requested_id", "")

        account = account_info.get("account")
        if account:
            review["payment_account_id"] = account.get("id", "")
            review["payment_account_name"] = account.get("name", "")
            review["payment_account_currency"] = account.get("currency", "")
            review["payment_account_last4"] = account.get("last4", "")
            review["payment_account_per_transaction_limit_usd"] = float(account.get("per_transaction_limit_usd", 0.0) or 0.0)
            review["payment_account_daily_limit_usd"] = float(account.get("daily_limit_usd", 0.0) or 0.0)
            review["payment_account_monthly_limit_usd"] = float(account.get("monthly_limit_usd", 0.0) or 0.0)
            if not account.get("ready", False):
                review["spend_policy_decision"] = "deny"
                review["reason"] = f"la cuenta de pago '{account.get('name') or account.get('id')}' no esta habilitada para uso automatico"
                return {"blocked": True}
            if amount_usd is not None:
                for field_name, label in (
                    ("per_transaction_limit_usd", "limite por transaccion"),
                    ("daily_limit_usd", "limite diario"),
                    ("monthly_limit_usd", "limite mensual"),
                ):
                    limit_value = float(account.get(field_name, 0.0) or 0.0)
                    if limit_value > 0 and amount_usd > limit_value:
                        review["spend_policy_decision"] = "deny"
                        review["reason"] = (
                            f"el monto ${amount_usd:.2f} excede el {label} de la cuenta '{account.get('name')}' (${limit_value:.2f})"
                        )
                        return {"blocked": True}
        elif account_info.get("requested_id"):
            review["spend_policy_decision"] = "deny"
            review["reason"] = f"la cuenta de pago '{account_info['requested_id']}' no esta registrada"
            return {"blocked": True}
        elif account_info.get("available_accounts", 0) > 1 and not account_info.get("default_account_id"):
            review["approval_override"] = "require"
            review["critic_override"] = "force"
            review["spend_policy_decision"] = "require_approval"
            review["reason"] = (
                f"{review['reason']}; hay varias cuentas registradas y no se indico `account_id` ni `default_account_id`"
            )
            return {"blocked": False}

        if policy["mode"] == "deny_all":
            review["spend_policy_decision"] = "deny"
            review["reason"] = "la politica de gasto actual no permite pagos automaticos"
            return {"blocked": True}

        if policy["mode"] == "ask_always":
            review["approval_override"] = "require"
            review["critic_override"] = "force"
            review["spend_policy_decision"] = "require_approval"
            review["reason"] = f"{review['reason']}; la politica de gasto exige aprobacion para cada pago"
            return {"blocked": False}

        if amount_usd is None:
            review["approval_override"] = "require"
            review["critic_override"] = "force"
            review["spend_policy_decision"] = "require_approval"
            if raw_amount is not None and currency and currency != "USD":
                review["reason"] = (
                    f"{review['reason']}; se detecto un monto en {currency} pero no se puede convertir a USD automaticamente"
                )
            else:
                review["reason"] = f"{review['reason']}; no se pudo verificar un monto en USD para aplicar la politica de gasto"
            return {"blocked": False}

        if policy["mode"] == "ask_above_x":
            review["critic_override"] = "force"
            if amount_usd > policy["ask_above_usd"]:
                review["approval_override"] = "require"
                review["spend_policy_decision"] = "require_approval"
                review["reason"] = (
                    f"{review['reason']}; el monto ${amount_usd:.2f} supera el umbral de aprobacion ${policy['ask_above_usd']:.2f}"
                )
            else:
                review["approval_override"] = "skip"
                review["spend_policy_decision"] = "allow_without_approval"
                review["reason"] = (
                    f"{review['reason']}; el monto ${amount_usd:.2f} queda por debajo o igual al umbral ${policy['ask_above_usd']:.2f}"
                )
            return {"blocked": False}

        if policy["mode"] == "auto_approve_under_x":
            if amount_usd <= policy["auto_approve_under_usd"]:
                review["approval_override"] = "skip"
                review["critic_override"] = "skip"
                review["spend_policy_decision"] = "auto_approved"
                review["reason"] = (
                    f"{review['reason']}; auto-aprobado porque ${amount_usd:.2f} esta dentro del limite ${policy['auto_approve_under_usd']:.2f}"
                )
            else:
                review["approval_override"] = "require"
                review["critic_override"] = "force"
                review["spend_policy_decision"] = "require_approval"
                review["reason"] = (
                    f"{review['reason']}; el monto ${amount_usd:.2f} excede el auto-approve de ${policy['auto_approve_under_usd']:.2f}"
                )
            return {"blocked": False}

        return {"blocked": False}

    def _extract_payment_amount(self, action: Action) -> dict[str, Any]:
        params = action.params if isinstance(action.params, dict) else {}
        candidate_payloads: list[tuple[str, Any]] = [
            ("params", params),
            ("input", params.get("input")),
            ("arguments", params.get("arguments")),
            ("payload", params.get("payload")),
        ]
        for source_prefix, payload in candidate_payloads:
            details = self._extract_payment_amount_from_value(payload, source_prefix)
            if details:
                return details
        return {
            "amount_usd": None,
            "raw_amount": None,
            "currency": "",
            "source": "",
            "verified_usd": False,
        }

    def _extract_payment_amount_from_value(
        self,
        value: Any,
        source_prefix: str,
        depth: int = 0,
    ) -> dict[str, Any] | None:
        if depth > 5:
            return None

        if isinstance(value, dict):
            currency = ""
            for key, nested in value.items():
                normalized_key = str(key).strip().lower()
                if normalized_key in PAYMENT_CURRENCY_KEYS:
                    currency = str(nested).strip().upper()
                    break

            for key, nested in value.items():
                normalized_key = str(key).strip().lower()
                amount = _coerce_non_negative_float(nested, default=-1.0)
                if normalized_key in PAYMENT_USD_KEYS and amount >= 0:
                    return {
                        "amount_usd": amount,
                        "raw_amount": amount,
                        "currency": "USD",
                        "source": f"{source_prefix}.{key}",
                        "verified_usd": True,
                    }

            for key, nested in value.items():
                details = self._extract_payment_amount_from_value(nested, f"{source_prefix}.{key}", depth + 1)
                if details:
                    return details

            for key, nested in value.items():
                normalized_key = str(key).strip().lower()
                amount = _coerce_non_negative_float(nested, default=-1.0)
                if normalized_key in PAYMENT_GENERIC_KEYS and amount >= 0:
                    if currency and currency != "USD":
                        return {
                            "amount_usd": None,
                            "raw_amount": amount,
                            "currency": currency,
                            "source": f"{source_prefix}.{key}",
                            "verified_usd": False,
                        }
                    return {
                        "amount_usd": amount,
                        "raw_amount": amount,
                        "currency": currency or "USD",
                        "source": f"{source_prefix}.{key}",
                        "verified_usd": not bool(currency) or currency == "USD",
                    }
            return None

        if isinstance(value, list):
            for index, item in enumerate(value):
                details = self._extract_payment_amount_from_value(item, f"{source_prefix}[{index}]", depth + 1)
                if details:
                    return details
        return None

    def _resolve_payment_account(self, action: Action) -> dict[str, Any]:
        registry_state = self._payments.list_accounts()
        requested_id = self._extract_payment_account_ref(action)
        default_account_id = str(registry_state.get("default_account_id", "") or "")
        effective_id = requested_id or default_account_id
        account = None
        if effective_id:
            try:
                account = self._payments.get_account(effective_id)
            except KeyError:
                account = None
        available_accounts = len([item for item in registry_state.get("accounts", []) if item.get("ready")])
        return {
            "requested_id": requested_id,
            "default_account_id": default_account_id,
            "available_accounts": available_accounts,
            "account": account,
        }

    def _extract_payment_account_ref(self, action: Action) -> str:
        params = action.params if isinstance(action.params, dict) else {}
        candidate_payloads: list[Any] = [
            params,
            params.get("input"),
            params.get("arguments"),
            params.get("payload"),
        ]
        for payload in candidate_payloads:
            account_id = self._extract_payment_account_ref_from_value(payload)
            if account_id:
                return account_id
        return ""

    def _extract_payment_account_ref_from_value(self, value: Any, depth: int = 0) -> str:
        if depth > 5:
            return ""
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized_key = str(key).strip().lower()
                if normalized_key in PAYMENT_ACCOUNT_KEYS:
                    return "".join(char.lower() if char.isalnum() else "-" for char in str(nested).strip()).strip("-")
            for nested in value.values():
                account_id = self._extract_payment_account_ref_from_value(nested, depth + 1)
                if account_id:
                    return account_id
        elif isinstance(value, list):
            for item in value:
                account_id = self._extract_payment_account_ref_from_value(item, depth + 1)
                if account_id:
                    return account_id
        return ""

    def _looks_payment_tool(self, tool_name: str, arguments: Any) -> bool:
        normalized = str(tool_name or "").strip().lower()
        if any(token in normalized for token in PAYMENT_TOOL_HINTS):
            return True
        details = self._extract_payment_amount_from_value(arguments, "arguments")
        return details is not None

    def _review(
        self,
        action: Action,
        capability: str | None,
        category: str,
        severity: str,
        confidence: float,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "action": action.type,
            "severity": severity,
            "reason": reason,
            "params": action.params,
            "capability": capability,
            "capability_label": CAPABILITY_LABELS.get(capability, capability) if capability else None,
            "category": category,
            "confidence": confidence,
        }


def is_approval_text(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in APPROVE_WORDS


def is_rejection_text(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in REJECT_WORDS
