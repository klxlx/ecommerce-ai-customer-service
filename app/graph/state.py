"""LangGraph agent state and routing constants."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypedDict

INTENTS = Literal[
    "greeting",
    "product_knowledge",
    "policy_knowledge",
    "price_stock",
    "order",
    "after_sale",
    "chit_chat",
    "clarification",
    "handoff",
    "knowledge_gap",
]

CHIT_CHAT_LIMIT = 2
MAX_CLARIFICATION_ATTEMPTS = 3


class AgentState(TypedDict, total=False):
    user_message: str
    user_id: int
    history: list[dict[str, Any]]
    intent: INTENTS
    slots: dict[str, Any]
    sources: list[dict[str, Any]]
    tool_evidence: dict[str, Any]
    grounding_context: str
    after_sale_context: dict[str, Any] | None
    chit_chat_streak: int
    clarification_attempts: int
    answer: str
    handoff: dict[str, Any] | None
    clarify_question: str | None
    # Runtime dependencies injected per invocation (not persisted).
    session: Any
    chat: Any
    providers: Any
    agents: Any
    weather: Any
    local_time: Callable[[], dict[str, str]]
    stream: Callable[[str], Awaitable[None]] | None
