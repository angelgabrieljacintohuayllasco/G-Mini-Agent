"""
Prompt registry and configurable prompt loading for G-Mini Agent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.config import ROOT_DIR, config


CORE_PROMPT_SPECS: dict[str, dict[str, str]] = {
    "system_base": {
        "label": "System Prompt Base",
        "default_file": "data/prompts/system_prompt.md",
    },
    "task_request_hint": {
        "label": "Task Request Hint",
        "default_file": "data/prompts/task_request_hint.md",
    },
    "browser_task_hint": {
        "label": "Browser Task Hint",
        "default_file": "data/prompts/browser_task_hint.md",
    },
    "no_actions_reinforcement": {
        "label": "No Actions Reinforcement",
        "default_file": "data/prompts/no_actions_reinforcement.md",
    },
    "screenshot_feedback": {
        "label": "Screenshot Feedback",
        "default_file": "data/prompts/screenshot_feedback.md",
    },
    "action_feedback_user": {
        "label": "Action Feedback User",
        "default_file": "data/prompts/action_feedback_user.md",
    },
    "browser_recovery_feedback": {
        "label": "Browser Recovery Feedback",
        "default_file": "data/prompts/browser_recovery_feedback.md",
    },
    "file_recovery_feedback": {
        "label": "File Recovery Feedback",
        "default_file": "data/prompts/file_recovery_feedback.md",
    },
    "dry_run_preview": {
        "label": "Dry Run Preview",
        "default_file": "data/prompts/dry_run_preview.md",
    },
    "stagnation_feedback": {
        "label": "Stagnation Feedback",
        "default_file": "data/prompts/stagnation_feedback.md",
    },
    "delegation_planner": {
        "label": "Delegation Planner",
        "default_file": "data/prompts/delegation_planner.md",
    },
    "subagent_worker_system": {
        "label": "Sub-Agent Worker System",
        "default_file": "data/prompts/subagent_worker_system.md",
    },
    "subagent_executor_system": {
        "label": "Sub-Agent Executor System",
        "default_file": "data/prompts/subagent_executor_system.md",
    },
    "computer_use_system": {
        "label": "Computer Use Sub-Agent System",
        "default_file": "data/prompts/computer_use_system.md",
    },
    "subagent_worker_user": {
        "label": "Sub-Agent Worker User",
        "default_file": "data/prompts/subagent_worker_user.md",
    },
    "critic_system": {
        "label": "Critic Agent System",
        "default_file": "data/prompts/critic_system.md",
    },
    "critic_user": {
        "label": "Critic Agent User",
        "default_file": "data/prompts/critic_user.md",
    },
    "voice_realtime": {
        "label": "Voz en Tiempo Real",
        "default_file": "data/prompts/voice_realtime.md",
    },
    "mcp_tools_context": {
        "label": "MCP Tools Context",
        "default_file": "data/prompts/mcp_tools_context.md",
    },
}


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _load_text_file(relative_path: str | None) -> str:
    if not relative_path:
        return ""
    path = ROOT_DIR / relative_path
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return ""


def _split_prompt_key(prompt_key: str) -> list[str]:
    return [part for part in prompt_key.split(".") if part]


def get_prompt_override(prompt_key: str) -> str:
    path = ["prompts", *_split_prompt_key(prompt_key)]
    value = config.get(*path, default="")
    if isinstance(value, str):
        return value.strip()
    return ""


def get_prompt_text(prompt_key: str, fallback: str = "") -> tuple[str, str]:
    override = get_prompt_override(prompt_key)
    if override:
        return override, "config"

    if prompt_key == "system_base":
        file_path = config.get("agent", "system_prompt_file", default="data/prompts/system_prompt.md")
        text = _load_text_file(file_path)
        if text:
            return text, "file"

    spec = CORE_PROMPT_SPECS.get(prompt_key)
    if spec:
        text = _load_text_file(spec.get("default_file"))
        if text:
            return text, "file"

    return fallback.strip(), "fallback"


def render_prompt_text(prompt_key: str, *, fallback: str = "", variables: dict[str, Any] | None = None) -> str:
    template, _ = get_prompt_text(prompt_key, fallback=fallback)
    if not variables:
        return template
    return template.format_map(_SafeFormatDict({key: value for key, value in variables.items()}))


def set_prompt_override(prompt_key: str, content: str) -> None:
    if not content.strip():
        reset_prompt_override(prompt_key)
        return
    path = ["prompts", *_split_prompt_key(prompt_key)]
    config.set(*path, value=content)


def reset_prompt_override(prompt_key: str) -> None:
    path = ["prompts", *_split_prompt_key(prompt_key)]
    config.unset(*path)


def list_core_prompts() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for prompt_key, spec in CORE_PROMPT_SPECS.items():
        content, source = get_prompt_text(prompt_key)
        items.append(
            {
                "key": prompt_key,
                "label": spec["label"],
                "content": content,
                "source": source,
                "default_file": spec.get("default_file", ""),
            }
        )
    return items
