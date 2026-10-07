"""
Shared resilience primitives for production-grade execution flows.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any

from pydantic import BaseModel, Field


class AgentOperationalError(RuntimeError):
    """Base exception for operational failures inside the agent runtime."""


class RetryableOperationError(AgentOperationalError):
    """Signals a transient failure that can be retried safely."""


class NonRetryableOperationError(AgentOperationalError):
    """Signals a terminal failure that should not be retried."""


class VisionCaptureError(RetryableOperationError):
    """Signals a screen capture failure."""


class OCRExecutionError(RetryableOperationError):
    """Signals an OCR execution failure."""


class ActionValidationError(NonRetryableOperationError):
    """Signals that an action is malformed or unsafe to execute."""


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=1, ge=1, le=8)
    initial_delay_ms: int = Field(default=0, ge=0, le=60000)
    backoff_multiplier: float = Field(default=2.0, ge=1.0, le=8.0)
    max_delay_ms: int = Field(default=0, ge=0, le=60000)
    jitter_ratio: float = Field(default=0.0, ge=0.0, le=0.5)

    def delay_seconds(self, attempt_number: int) -> float:
        """
        Delay before the retry that follows `attempt_number` (1-based).

        Exponential backoff from `initial_delay_ms`, capped by `max_delay_ms`
        (or by `initial_delay_ms` when no cap is set), with optional jitter.
        """
        if attempt_number < 1 or self.max_attempts <= 1:
            return 0.0

        base_delay_ms = self.initial_delay_ms * math.pow(
            self.backoff_multiplier,
            max(0, attempt_number - 1),
        )
        capped_ms = min(
            max(0.0, base_delay_ms),
            float(self.max_delay_ms or self.initial_delay_ms or 0),
        )
        delay_seconds = capped_ms / 1000.0
        if delay_seconds <= 0 or self.jitter_ratio <= 0:
            return delay_seconds

        jitter = delay_seconds * self.jitter_ratio
        return max(0.0, delay_seconds + random.uniform(-jitter, jitter))


class ActionAttemptRecord(BaseModel):
    attempt: int = Field(..., ge=1)
    success: bool
    duration_ms: float = Field(default=0.0, ge=0.0)
    message: str = ""
    error_kind: str | None = None
    timestamp: float = Field(default_factory=time.time)


class ActionExecutionResultModel(BaseModel):
    action: str
    success: bool = False
    message: str = ""
    data: dict[str, Any] | None = None
    task_complete: bool = False
    attempts: int = Field(default=1, ge=1)
    retry_count: int = Field(default=0, ge=0)
    failure_kind: str | None = None
    recoverable: bool = False
    duration_ms: float = Field(default=0.0, ge=0.0)
    attempt_log: list[ActionAttemptRecord] = Field(default_factory=list)


class VisionHealthSnapshot(BaseModel):
    initialized: bool = False
    ocr_type: str = "none"
    capture_failures: int = Field(default=0, ge=0)
    ocr_failures: int = Field(default=0, ge=0)
    recoveries: int = Field(default=0, ge=0)
    last_error: str = ""
    last_recovery_ts: float | None = None
    last_capture_ts: float | None = None
    last_capture_grab_ms: float = Field(default=0.0, ge=0.0)
    last_capture_encode_ms: float = Field(default=0.0, ge=0.0)
    last_capture_resize_ms: float = Field(default=0.0, ge=0.0)
    last_capture_total_ms: float = Field(default=0.0, ge=0.0)
    last_capture_path: str = ""
    last_capture_monitor: int = 0
    capture_slo_target_ms: float = Field(default=50.0, ge=0.0)
    capture_slo_met: bool = False
    degraded: bool = False
