"""Immutable context-pack models shared by prompts and traces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

HistoryMessage = dict[str, str]


@dataclass(frozen=True)
class ContextManifest:
    """What the prompt used, without storing the prompt itself.

    The manifest is safe to persist in ``AgentRun.trace``: it contains counts,
    block names and slot/tool keys, never raw prompt text or user content.
    """

    purpose: str
    base_chars: int
    slots_chars: int
    tool_facts_chars: int
    history_chars: int
    user_chars: int
    total_chars: int
    history_turns: int
    history_messages: int
    history_messages_dropped: int
    slots_included: tuple[str, ...]
    tool_sections: tuple[str, ...]
    ignored_tool_sections: tuple[str, ...]
    truncated: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "base_chars": self.base_chars,
            "slots_chars": self.slots_chars,
            "tool_facts_chars": self.tool_facts_chars,
            "history_chars": self.history_chars,
            "user_chars": self.user_chars,
            "total_chars": self.total_chars,
            "history_turns": self.history_turns,
            "history_messages": self.history_messages,
            "history_messages_dropped": self.history_messages_dropped,
            "slots_included": list(self.slots_included),
            "tool_sections": list(self.tool_sections),
            "ignored_tool_sections": list(self.ignored_tool_sections),
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class ContextPack:
    """A ready-to-send prompt plus a manifest describing its construction."""

    purpose: str
    system_content: str
    history: tuple[HistoryMessage, ...]
    user_message: str
    manifest: ContextManifest

    def to_messages(self) -> list[HistoryMessage]:
        messages: list[HistoryMessage] = [
            {"role": "system", "content": self.system_content}
        ]
        messages.extend(
            {"role": message["role"], "content": message["content"]}
            for message in self.history
        )
        messages.append({"role": "user", "content": self.user_message})
        return messages
