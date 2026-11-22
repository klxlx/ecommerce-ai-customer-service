"""Build the bounded context pack used by answer, router and planner prompts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.context.budget import (
    ContextLimits,
    count_user_turns,
    normalize_history,
    select_recent_turns,
    trim_history_to_chars,
)
from app.context.formatters import format_slots, format_tool_facts
from app.context.models import ContextManifest, ContextPack

DEFAULT_TOOL_LABEL = "当前工具事实"


def build_context_pack(
    *,
    purpose: str,
    system_prompt: str,
    history: Sequence[Mapping[str, Any]] | None,
    user_message: str,
    slots: Mapping[str, Any] | None = None,
    tool_evidence: Mapping[str, Any] | None = None,
    settings: Any | None = None,
    tool_label: str = DEFAULT_TOOL_LABEL,
    system_suffix: str | None = None,
) -> ContextPack:
    """Assemble a prompt without storing raw prompt text in the manifest.

    Order:
    system prompt -> slots -> tool facts -> optional system suffix -> history
    -> current user message.

    The current user message and system prompt are never truncated. History is
    the first block sacrificed, followed by tool facts via their own budget.
    """
    limits = ContextLimits.from_settings(settings)
    current = str(user_message or "").strip()
    base = str(system_prompt or "").strip()

    formatted_slots = format_slots(slots, max_chars=limits.slots_max_chars)
    formatted_tools = format_tool_facts(
        tool_evidence, max_chars=limits.tool_facts_max_chars
    )

    system_parts = [base]
    if formatted_slots.text:
        system_parts.append(f"【当前会话槽位】\n{formatted_slots.text}")
    if formatted_tools.text:
        system_parts.append(f"【{tool_label}】\n{formatted_tools.text}")
    if system_suffix and system_suffix.strip():
        system_parts.append(system_suffix.strip())

    system_content = "\n\n".join(part for part in system_parts if part)

    normalized = normalize_history(history)
    selected, dropped_by_turns = select_recent_turns(
        normalized, limits.history_turns
    )

    # System rules and the current question are hard constraints. History gets
    # whatever budget remains, capped by its own limit.
    available_for_history = (
        limits.max_chars - len(system_content) - len(current)
    )
    history_budget = min(limits.history_max_chars, max(0, available_for_history))
    selected, dropped_by_chars = trim_history_to_chars(selected, history_budget)
    dropped_total = dropped_by_turns + dropped_by_chars

    history_chars = sum(len(message["content"]) for message in selected)
    total_chars = len(system_content) + history_chars + len(current)
    truncated = (
        formatted_slots.truncated
        or formatted_tools.truncated
        or dropped_total > 0
        or total_chars > limits.max_chars
    )

    manifest = ContextManifest(
        purpose=purpose,
        base_chars=len(base),
        slots_chars=len(formatted_slots.text),
        tool_facts_chars=len(formatted_tools.text),
        history_chars=history_chars,
        user_chars=len(current),
        total_chars=total_chars,
        history_turns=count_user_turns(selected),
        history_messages=len(selected),
        history_messages_dropped=dropped_total,
        slots_included=formatted_slots.keys,
        tool_sections=formatted_tools.sections,
        ignored_tool_sections=formatted_tools.ignored_sections,
        truncated=truncated,
    )
    return ContextPack(
        purpose=purpose,
        system_content=system_content,
        history=tuple(selected),
        user_message=current,
        manifest=manifest,
    )


def context_engine_enabled(settings: Any | None) -> bool:
    """Read the rollout flag; absent settings keep the legacy prompt path."""
    return bool(getattr(settings, "context_engine_enabled", False))
