"""Deterministic character budgets and history-window helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.context.models import HistoryMessage

_ALLOWED_ROLES = {"user", "assistant"}
_TRUNCATION_SUFFIX = "…"


@dataclass(frozen=True)
class ContextLimits:
    max_chars: int = 6000
    history_turns: int = 6
    history_max_chars: int = 3000
    tool_facts_max_chars: int = 1200
    slots_max_chars: int = 300

    @classmethod
    def from_settings(cls, settings: Any | None) -> "ContextLimits":
        return cls(
            max_chars=_positive_int(
                getattr(settings, "context_max_chars", None), cls.max_chars
            ),
            history_turns=_positive_int(
                getattr(settings, "context_history_turns", None),
                cls.history_turns,
            ),
            history_max_chars=_positive_int(
                getattr(settings, "context_history_max_chars", None),
                cls.history_max_chars,
            ),
            tool_facts_max_chars=_positive_int(
                getattr(settings, "context_tool_facts_max_chars", None),
                cls.tool_facts_max_chars,
            ),
            slots_max_chars=_positive_int(
                getattr(settings, "context_slots_max_chars", None),
                cls.slots_max_chars,
            ),
        )


def _positive_int(value: object, default: int) -> int:
    """Return a usable positive integer, falling back to a safe default."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return max(1, default)
    return max(1, parsed)


def normalize_history(
    history: Sequence[Mapping[str, Any]] | None,
) -> list[HistoryMessage]:
    """Keep only provider-safe user/assistant messages with text content."""
    normalized: list[HistoryMessage] = []
    for message in history or ():
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "").strip()
        content = message.get("content")
        if role not in _ALLOWED_ROLES or not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        normalized.append({"role": role, "content": text})
    return normalized


def select_recent_turns(
    messages: Sequence[HistoryMessage], max_turns: int
) -> tuple[list[HistoryMessage], int]:
    """Select the most recent N user turns and their following replies.

    A turn starts at a user message. Messages before the selected start are
    discarded together, so an assistant reply is never separated from the user
    message that produced it.
    """
    if max_turns <= 0:
        return [], len(messages)

    user_indexes = [
        index for index, message in enumerate(messages) if message["role"] == "user"
    ]
    if len(user_indexes) <= max_turns:
        selected = list(messages)
    else:
        start = user_indexes[-max_turns]
        selected = list(messages[start:])

    # A persisted greeting or an admin/system message mapped to assistant can
    # leave the window starting with an assistant turn. Drop it so the provider
    # never receives an answer without its originating question.
    while selected and selected[0]["role"] == "assistant":
        selected.pop(0)
    return selected, len(messages) - len(selected)


def trim_history_to_chars(
    messages: Sequence[HistoryMessage], max_chars: int
) -> tuple[list[HistoryMessage], int]:
    """Drop the oldest messages until the history fits the character budget."""
    if max_chars <= 0:
        return [], len(messages)

    selected = list(messages)
    total = sum(len(message["content"]) for message in selected)
    dropped = 0
    while selected and total > max_chars:
        removed = selected.pop(0)
        total -= len(removed["content"])
        dropped += 1

    # Never start a provider prompt with an assistant turn left over after
    # trimming its user message.
    while selected and selected[0]["role"] == "assistant":
        selected.pop(0)
        dropped += 1
    return selected, dropped


def truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    """Trim text to a character budget without splitting the suffix."""
    if max_chars <= 0:
        return "", bool(text)
    if len(text) <= max_chars:
        return text, False
    if max_chars <= len(_TRUNCATION_SUFFIX):
        return _TRUNCATION_SUFFIX[:max_chars], True
    return (
        text[: max_chars - len(_TRUNCATION_SUFFIX)].rstrip()
        + _TRUNCATION_SUFFIX,
        True,
    )


def count_user_turns(messages: Sequence[HistoryMessage]) -> int:
    return sum(1 for message in messages if message["role"] == "user")
