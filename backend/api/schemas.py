"""
G-Mini Agent — Pydantic schemas para API REST y WebSocket payloads.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────

class AgentStatus(str, Enum):
    IDLE = "idle"
    PAUSED = "paused"
    THINKING = "thinking"
    RESPONDING = "responding"
    EXECUTING = "executing"
    ACTING = "acting"
    LISTENING = "listening"
    CALLING = "calling"
    ERROR = "error"


class LipsyncState(str, Enum):
    IDLE = "idle"
    TALK = "talk"
    BLINK = "blink"
    BLINK_TALK = "blink_talk"


# ── WebSocket Events: User → Backend ────────────────────────────

class UserMessage(BaseModel):
    text: str
    attachments: list[Any] = Field(default_factory=list)  # base64 images, file paths o metadata de adjuntos


class UserCommand(BaseModel):
    action: str  # start | stop | pause | voice_start | voice_stop


class UserConfig(BaseModel):
    section: str
    key: str
    value: Any


class UserSTTAudio(BaseModel):
    audio_b64: str
    format: str = "wav"


class UserRealtimeAudio(BaseModel):
    audio_chunk_b64: str


# ── WebSocket Events: Backend → Frontend ─────────────────────────

class AgentMessage(BaseModel):
    text: str
    type: str = "text"  # text | action | error | system
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    done: bool = False  # True cuando el streaming termina


class AgentStatusEvent(BaseModel):
    status: AgentStatus


class AgentApprovalEvent(BaseModel):
    pending: bool = False
    mode: str | None = None
    mode_name: str | None = None
    kind: str = "approval"
    decision: str | None = None
    summary: str = ""
    findings: list[dict[str, Any]] = Field(default_factory=list)


class AgentScreenshot(BaseModel):
    image_b64: str
    elements: list[dict] = Field(default_factory=list)
    ocr_text: str = ""


class AgentLipsync(BaseModel):
    state: LipsyncState


class AgentAudio(BaseModel):
    audio_b64: str
    duration: float = 0.0


class AgentSTTResult(BaseModel):
    text: str
    confidence: float = 1.0


class AgentRealtimeAudio(BaseModel):
    audio_chunk_b64: str


# ── REST API models ──────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    uptime_seconds: float


class ModelInfo(BaseModel):
    id: str
    provider: str
    is_local: bool = False
    supports_vision: bool = False
    supports_streaming: bool = True


class ModelsResponse(BaseModel):
    models: list[ModelInfo]
    current_provider: str
    current_model: str


class ConfigUpdateRequest(BaseModel):
    section: str
    key: str
    value: Any


class PromptUpdateRequest(BaseModel):
    key: str
    content: str


class CustomModeUpsertRequest(BaseModel):
    name: str
    description: str = ""
    icon: str = ""
    behavior_prompt: str = ""
    system_prompt: str = ""
    allowed_capabilities: list[str] = Field(default_factory=list)
    restricted_capabilities: list[str] = Field(default_factory=list)
    requires_scope_confirmation: bool = False


class ConfigResponse(BaseModel):
    success: bool
    data: dict | None = None
    error: str | None = None


class PaymentAccountInfo(BaseModel):
    id: str
    name: str
    type: str = "generic"
    provider: str = ""
    currency: str = "USD"
    enabled: bool = True
    status: str = "ready"
    ready: bool = False
    detail: str = ""
    last4: str = ""
    per_transaction_limit_usd: float = 0.0
    daily_limit_usd: float = 0.0
    monthly_limit_usd: float = 0.0
    allowed_uses: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PaymentAccountsResponse(BaseModel):
    enabled: bool = True
    default_account_id: str = ""
    accounts: list[PaymentAccountInfo] = Field(default_factory=list)


class APIKeySetRequest(BaseModel):
    provider: str
    api_key: str


class ChatMessage(BaseModel):
    role: str  # user | assistant | system
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class ConversationHistory(BaseModel):
    messages: list[ChatMessage]
    session_id: str


class ScheduledJobInfo(BaseModel):
    job_id: str
    name: str
    task_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    trigger_type: str
    interval_seconds: int | None = None
    cron_expression: str | None = None
    event_name: str = ""
    webhook_path: str = ""
    webhook_secret: str = ""
    heartbeat_key: str = "system"
    heartbeat_interval_seconds: int | None = None
    enabled: bool = True
    max_retries: int = 0
    retry_backoff_seconds: int = 30
    retry_backoff_multiplier: float = 2.0
    retry_attempt: int = 0
    next_run_at: str | None = None
    last_signal_at: str | None = None
    last_run_at: str | None = None
    last_error: str = ""
    created_at: str
    updated_at: str


class ScheduledRunInfo(BaseModel):
    run_id: str
    job_id: str
    trigger_source: str
    status: str
    started_at: str
    finished_at: str | None = None
    duration_ms: int = 0
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class SchedulerJobsResponse(BaseModel):
    jobs: list[ScheduledJobInfo] = Field(default_factory=list)


class SchedulerRunsResponse(BaseModel):
    runs: list[ScheduledRunInfo] = Field(default_factory=list)


class ScheduledCheckpointInfo(BaseModel):
    checkpoint_id: str
    job_id: str
    run_id: str | None = None
    checkpoint_type: str
    status: str
    progress: float = 0.0
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class SchedulerRecoveryInfo(BaseModel):
    checked_at: str | None = None
    interrupted_runs: int = 0
    rescheduled_jobs: int = 0
    retry_scheduled_jobs: int = 0
    recovered_run_ids: list[str] = Field(default_factory=list)


class SchedulerCheckpointsResponse(BaseModel):
    checkpoints: list[ScheduledCheckpointInfo] = Field(default_factory=list)
    recovery: SchedulerRecoveryInfo = Field(default_factory=SchedulerRecoveryInfo)


class CostAggregateInfo(BaseModel):
    event_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_cost_usd: float = 0.0
    output_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    estimated_events: int = 0
    unpriced_events: int = 0
    last_event_at: str | None = None


class CostBreakdownInfo(BaseModel):
    key: str = ""
    label: str = ""
    event_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_cost_usd: float = 0.0
    output_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    estimated_events: int = 0
    unpriced_events: int = 0
    share_percent: float = 0.0
    last_event_at: str | None = None


class CostDailyUsageInfo(BaseModel):
    date: str
    label: str = ""
    event_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_cost_usd: float = 0.0
    output_cost_usd: float = 0.0
    total_cost_usd: float = 0.0


class CostBudgetBucket(BaseModel):
    limit_usd: float = 0.0
    usage_percent: float = 0.0
    remaining_usd: float | None = None
    state: str = "unlimited"
    stop_required: bool = False


class CostBudgetStatus(BaseModel):
    enabled: bool = True
    warning_threshold_percent: float = 80.0
    session: CostBudgetBucket = Field(default_factory=CostBudgetBucket)
    daily: CostBudgetBucket = Field(default_factory=CostBudgetBucket)
    monthly: CostBudgetBucket = Field(default_factory=CostBudgetBucket)
    mode: CostBudgetBucket = Field(default_factory=CostBudgetBucket)
    current_worker: CostBudgetBucket = Field(default_factory=CostBudgetBucket)
    task_limit_usd: float = 0.0
    mode_limit_usd: float = 0.0
    subagent_effective_limit_usd: float = 0.0
    daily_limit_usd: float = 0.0
    monthly_limit_usd: float = 0.0
    alerts: list[str] = Field(default_factory=list)
    stop_required: bool = False


class CostSummaryResponse(BaseModel):
    enabled: bool = True
    session_id: str | None = None
    current_mode: str = ""
    current_worker_id: str | None = None
    current_worker_kind: str = ""
    configured_models: int = 0
    current_session: CostAggregateInfo = Field(default_factory=CostAggregateInfo)
    current_mode_usage: CostAggregateInfo = Field(default_factory=CostAggregateInfo)
    current_worker: CostAggregateInfo = Field(default_factory=CostAggregateInfo)
    today: CostAggregateInfo = Field(default_factory=CostAggregateInfo)
    month: CostAggregateInfo = Field(default_factory=CostAggregateInfo)
    budget_status: CostBudgetStatus = Field(default_factory=CostBudgetStatus)
    alerts: list[str] = Field(default_factory=list)
    checked_at: str | None = None


class CostWeeklyReportResponse(BaseModel):
    session_id: str | None = None
    current_mode: str = ""
    week_offset: int = 0
    include_current_week: bool = False
    window_label: str = ""
    week_start: str | None = None
    week_end: str | None = None
    week_start_local: str = ""
    week_end_local: str = ""
    previous_week_start: str | None = None
    previous_week_end: str | None = None
    previous_week_start_local: str = ""
    previous_week_end_local: str = ""
    totals: CostAggregateInfo = Field(default_factory=CostAggregateInfo)
    previous_totals: CostAggregateInfo = Field(default_factory=CostAggregateInfo)
    delta_total_cost_usd: float = 0.0
    delta_percent: float | None = None
    provider_breakdown: list[CostBreakdownInfo] = Field(default_factory=list)
    mode_breakdown: list[CostBreakdownInfo] = Field(default_factory=list)
    worker_kind_breakdown: list[CostBreakdownInfo] = Field(default_factory=list)
    daily_breakdown: list[CostDailyUsageInfo] = Field(default_factory=list)
    delivery_targets: list[str] = Field(default_factory=list)
    delivery_status: str = "preview_only"
    highlights: list[str] = Field(default_factory=list)
    checked_at: str | None = None


class CostEventInfo(BaseModel):
    event_id: str
    session_id: str
    provider: str
    model: str
    source: str
    mode_key: str = ""
    worker_id: str = ""
    worker_kind: str = "agent"
    parent_worker_id: str = ""
    parent_task_limit_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False
    pricing_missing: bool = False
    cost_input_usd: float = 0.0
    cost_output_usd: float = 0.0
    total_cost_usd: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class CostEventsResponse(BaseModel):
    session_id: str | None = None
    events: list[CostEventInfo] = Field(default_factory=list)


class CostOptimizerStatusResponse(BaseModel):
    enabled: bool = False
    total_switches: int = 0
    last_pressure: dict[str, Any] | None = None
    downgrade_chain_size: int = 0
    local_fallback_configured: bool = False
    compression_token_threshold: int = 40000


class CostOptimizerPressureResponse(BaseModel):
    level: str = "none"
    max_usage_percent: float = 0.0
    scopes: list[dict[str, Any]] = Field(default_factory=list)
    stop_required: bool = False
    alerts: list[str] = Field(default_factory=list)


class ScheduledJobCreateRequest(BaseModel):
    name: str
    task_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    trigger_type: str
    interval_seconds: int | None = None
    cron_expression: str | None = None
    event_name: str | None = None
    webhook_path: str | None = None
    webhook_secret: str | None = None
    heartbeat_key: str | None = None
    heartbeat_interval_seconds: int | None = None
    enabled: bool = True
    max_retries: int = 0
    retry_backoff_seconds: int = 30
    retry_backoff_multiplier: float = 2.0
    timezone: str | None = None  # IANA, p. ej. America/Lima; vacío = la del equipo


class ScheduledJobUpdateRequest(BaseModel):
    name: str | None = None
    payload: dict[str, Any] | None = None
    interval_seconds: int | None = None
    cron_expression: str | None = None
    event_name: str | None = None
    webhook_path: str | None = None
    webhook_secret: str | None = None
    heartbeat_key: str | None = None
    heartbeat_interval_seconds: int | None = None
    enabled: bool | None = None
    max_retries: int | None = None
    retry_backoff_seconds: int | None = None
    retry_backoff_multiplier: float | None = None


class ScheduledJobMutationResponse(BaseModel):
    success: bool
    action: str
    job: ScheduledJobInfo | None = None
    run: ScheduledRunInfo | None = None
    data: dict[str, Any] | None = None
    error: str | None = None


class SchedulerTriggerRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = None


class SchedulerTriggerResponse(BaseModel):
    success: bool
    trigger_type: str
    trigger_value: str
    matched_jobs: int = 0
    executed_jobs: int = 0
    queued_jobs: int = 0
    skipped_jobs: int = 0
    rejected_jobs: int = 0
    runs: list[ScheduledRunInfo] = Field(default_factory=list)
    error: str | None = None


class GatewayChannelInfo(BaseModel):
    channel: str
    enabled: bool = False
    implemented: bool = False
    ready: bool = False
    connected_sessions: int = 0
    queued_notifications: int = 0
    detail: str = ""


class GatewaySessionInfo(BaseModel):
    sid: str
    channel: str
    session_key: str
    display_name: str = ""
    connected: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)
    connected_at: str | None = None
    last_seen_at: str | None = None
    disconnected_at: str | None = None


class GatewayOutboxInfo(BaseModel):
    notification_id: str
    channel: str
    session_key: str
    level: str = "info"
    title: str = ""
    body: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    source_type: str = ""
    source_id: str = ""
    status: str = "queued"
    attempts: int = 0
    last_error: str = ""
    created_at: str | None = None
    delivered_at: str | None = None


class GatewayStatusResponse(BaseModel):
    enabled: bool = True
    default_channel: str = "local_app"
    default_session_key: str = "main"
    session_router_enabled: bool = True
    connected_sessions: int = 0
    queued_notifications: int = 0
    delivered_notifications: int = 0
    failed_notifications: int = 0
    channels: list[GatewayChannelInfo] = Field(default_factory=list)
    checked_at: str | None = None


class GatewaySessionsResponse(BaseModel):
    sessions: list[GatewaySessionInfo] = Field(default_factory=list)


class GatewayOutboxResponse(BaseModel):
    notifications: list[GatewayOutboxInfo] = Field(default_factory=list)


class GatewayNotifyRequest(BaseModel):
    title: str
    body: str = ""
    target: str | None = None
    level: str = "info"
    payload: dict[str, Any] = Field(default_factory=dict)
    source_type: str = ""
    source_id: str = ""


class GatewayNotifyResponse(BaseModel):
    success: bool
    notification: GatewayOutboxInfo | None = None
    error: str | None = None


class GatewayCredentialSetRequest(BaseModel):
    channel: str
    token: str


class GatewayCredentialStatusInfo(BaseModel):
    channel: str
    configured: bool = False
    masked: str | None = None


class GatewayCredentialStatusResponse(BaseModel):
    credentials: list[GatewayCredentialStatusInfo] = Field(default_factory=list)


class SkillToolInfo(BaseModel):
    name: str
    description: str = ""
    script: str | None = None
    entrypoint: str | None = None
    command: str | list[str] | None = None
    timeout_seconds: int | None = None


class SkillRootInfo(BaseModel):
    source: str
    path: str
    priority: int
    exists: bool = False


class SkillInfo(BaseModel):
    id: str
    name: str
    version: str = ""
    description: str = ""
    category: str = ""
    author: str = ""
    source: str
    priority: int
    enabled: bool = True
    root_path: str
    manifest_path: str
    readme_path: str | None = None
    readme_excerpt: str | None = None
    requires_api_keys: list[str] = Field(default_factory=list)
    requires_permissions: list[str] = Field(default_factory=list)
    tools: list[SkillToolInfo] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SkillsCatalogResponse(BaseModel):
    enabled: bool = False
    roots: list[SkillRootInfo] = Field(default_factory=list)
    skills: list[SkillInfo] = Field(default_factory=list)
    duplicates: dict[str, list[str]] = Field(default_factory=dict)


class MCPServerInfo(BaseModel):
    id: str
    name: str
    transport: str
    enabled: bool = True
    status: str
    ready: bool = False
    detail: str = ""
    command: str | None = None
    resolved_command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    cwd: str | None = None
    env_keys: list[str] = Field(default_factory=list)


class MCPServersResponse(BaseModel):
    enabled: bool = False
    servers: list[MCPServerInfo] = Field(default_factory=list)


class MCPToolInfo(BaseModel):
    name: str
    title: str | None = None
    description: str = ""
    inputSchema: dict[str, Any] | None = None
    outputSchema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None


class MCPToolsResponse(BaseModel):
    success: bool
    server_id: str
    server_name: str = ""
    transport: str = ""
    protocol_version: str = ""
    tools: list[MCPToolInfo] = Field(default_factory=list)
    next_cursor: str | None = None
    stderr: str = ""
    notifications: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class MCPToolCallRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = None


class MCPToolCallResponse(BaseModel):
    success: bool
    server_id: str
    server_name: str = ""
    transport: str = ""
    protocol_version: str = ""
    tool: str
    is_error: bool = False
    content: list[dict[str, Any]] = Field(default_factory=list)
    structured_content: dict[str, Any] | None = None
    raw_result: dict[str, Any] | None = None
    stderr: str = ""
    notifications: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class SkillInstallLocalRequest(BaseModel):
    path: str
    overwrite: bool = False


class SkillInstallGitRequest(BaseModel):
    repo_url: str
    ref: str = ""
    subdir: str = ""
    overwrite: bool = False


class SkillMutationResponse(BaseModel):
    success: bool
    action: str
    skill: SkillInfo | None = None
    data: dict[str, Any] | None = None
    error: str | None = None


class SkillRunRequest(BaseModel):
    tool: str
    input: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = None


class SkillRunResponse(BaseModel):
    success: bool
    skill_id: str
    tool: str
    exit_code: int | None = None
    data: dict[str, Any] | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_ms: int = 0
    command: list[str] = Field(default_factory=list)
    cwd: str = ""
    error: str | None = None


# ── Action Dispatcher ────────────────────────────────────


class ActionExecuteRequest(BaseModel):
    action_type: str = Field(..., description="Tipo de acción (click, type, screenshot, browser_navigate, etc.)")
    params: dict[str, Any] = Field(default_factory=dict, description="Parámetros de la acción")
    description: str = ""


class ActionExecuteResponse(BaseModel):
    success: bool
    action_type: str
    result: dict[str, Any] | None = None
    policy_blocked: bool = False
    policy_reason: str = ""
    error: str | None = None


class ActionListResponse(BaseModel):
    mode: str
    mode_name: str
    actions: list[dict[str, Any]]
    total: int
