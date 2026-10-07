"""
G-Mini Agent — Token Manager.
Conteo de tokens y truncado de historial para no exceder ventana de contexto.
"""

from __future__ import annotations

from loguru import logger

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
except Exception:
    _enc = None
    logger.warning("tiktoken no disponible, usando estimación por caracteres")


# Ventanas de contexto por modelo — datos verificados contra docs oficiales (2026-03)
MODEL_CONTEXT_WINDOWS = {
    # ── OpenAI (developers.openai.com) ──
    "gpt-5.4": 1_050_000,          # Frontier, razonamiento profesional
    "gpt-5.4-pro": 1_050_000,      # Pro: respuestas más precisas
    "gpt-5-mini": 1_047_576,       # Rápido, eficiente en costo
    "gpt-5-nano": 1_047_576,       # El más rápido/barato GPT-5
    "gpt-4.1": 1_047_576,          # Mejor modelo no-razonamiento
    "gpt-5.3-codex": 1_047_576,    # Más capaz para coding agéntico
    "gpt-5.2-codex": 1_047_576,    # Coding optimizado tareas largas
    # ── Anthropic (platform.claude.com) ──
    "claude-opus-4-6": 200_000,    # 1M con header beta context-1m-2025-08-07
    "claude-sonnet-4-6": 200_000,  # 1M con header beta context-1m-2025-08-07
    "claude-haiku-4-5-20251001": 200_000,
    # ── Google (ai.google.dev) ──
    "gemini-3.1-pro-preview": 1_000_000,
    "gemini-3-flash-preview": 1_000_000,
    "gemini-3.1-flash-lite-preview": 1_000_000,
    # ── xAI (docs.x.ai) ──
    "grok-4-1-fast-reasoning": 2_000_000,
    "grok-4-1-fast-non-reasoning": 2_000_000,
    "grok-4": 256_000,
    "grok-code-fast-1": 256_000,
    # ── DeepSeek (api-docs.deepseek.com) — V3.2 ──
    "deepseek-chat": 128_000,
    "deepseek-reasoner": 128_000,
}

DEFAULT_CONTEXT_WINDOW = 128_000
RESERVE_FOR_RESPONSE = 4096


def count_tokens(text: str) -> int:
    """Cuenta tokens en un texto."""
    if _enc:
        return len(_enc.encode(text))
    # Fallback: ~4 chars per token
    return len(text) // 4


def count_messages_tokens(messages: list[dict]) -> int:
    """Cuenta tokens totales en una lista de mensajes."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content) + 4  # overhead per message
        elif isinstance(content, list):
            # Multimodal: cada imagen ~1000 tokens estimados
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        total += count_tokens(part.get("text", ""))
                    elif part.get("type") in ("image_url", "image"):
                        total += 1000
            total += 4
        # El agente pasa imágenes y adjuntos en claves aparte (no dentro de content).
        total += 1000 * len(msg.get("images") or [])
        for attachment in msg.get("files") or []:
            if isinstance(attachment, dict):
                # base64 → bytes ≈ 3/4; ~4 bytes por token como estimación conservadora.
                total += max(250, int(len(str(attachment.get("data", ""))) * 0.75 / 4))
    return total


def get_context_window(model: str) -> int:
    """Retorna la ventana de contexto para un modelo."""
    return MODEL_CONTEXT_WINDOWS.get(model, DEFAULT_CONTEXT_WINDOW)


def _build_truncation_summary(dropped: list[dict]) -> str:
    """Build a compact summary of dropped messages to preserve context."""
    facts: list[str] = []
    for msg in dropped:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        # Extract key fragments: user questions and assistant actions
        if role == "user":
            snippet = content.strip()[:120]
            facts.append(f"[user] {snippet}")
        elif role == "assistant":
            # Preserve lines that contain actions
            action_lines = [
                ln.strip()[:100]
                for ln in content.splitlines()
                if ln.strip().startswith("[ACTION:")
            ]
            if action_lines:
                facts.append(f"[assistant] {'; '.join(action_lines[:5])}")
            else:
                snippet = content.strip()[:80]
                facts.append(f"[assistant] {snippet}")
    # Cap total summary length
    summary_parts = facts[:15]
    return (
        f"[Contexto previo resumido: {len(dropped)} mensajes eliminados. "
        f"Hechos clave: {' | '.join(summary_parts)}]"
    )


def truncate_messages(
    messages: list[dict],
    model: str,
    max_tokens: int | None = None,
) -> list[dict]:
    """
    Trunca mensajes antiguos para que quepan en la ventana de contexto.
    Siempre preserva: el system prompt (primero) y los últimos N mensajes.
    Genera un resumen compacto de los mensajes eliminados.
    """
    if max_tokens is None:
        max_tokens = get_context_window(model) - RESERVE_FOR_RESPONSE

    total_tokens = count_messages_tokens(messages)

    if total_tokens <= max_tokens:
        return messages

    # Separar system prompt
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    system_tokens = count_messages_tokens(system_msgs)
    available = max_tokens - system_tokens

    # Mantener los mensajes más recientes que quepan
    kept = []
    current_tokens = 0

    for msg in reversed(non_system):
        msg_tokens = count_messages_tokens([msg])
        if current_tokens + msg_tokens > available:
            break
        kept.insert(0, msg)
        current_tokens += msg_tokens

    truncated_count = len(non_system) - len(kept)
    if truncated_count > 0:
        dropped = non_system[:truncated_count]
        summary_text = _build_truncation_summary(dropped)
        summary_msg = {"role": "user", "content": summary_text}
        summary_tokens = count_messages_tokens([summary_msg])

        # Ensure summary fits; if not, trim kept to make room
        if current_tokens + summary_tokens > available and len(kept) > 1:
            while len(kept) > 1 and current_tokens + summary_tokens > available:
                removed = kept.pop(0)
                current_tokens -= count_messages_tokens([removed])

        if current_tokens + summary_tokens <= available:
            kept.insert(0, summary_msg)
            current_tokens += summary_tokens

        logger.info(
            f"Historial truncado: {truncated_count} mensajes eliminados "
            f"({total_tokens} → {current_tokens + system_tokens} tokens), "
            f"resumen insertado"
        )

    return system_msgs + kept
