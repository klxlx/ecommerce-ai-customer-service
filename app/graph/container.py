"""Provider container dataclass (isolated to avoid import cycles)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.registry import AgentRegistry
from app.core.config import Settings
from app.core.metrics import MetricsRegistry
from app.providers.chat import ChatProvider
from app.rag.retriever import Providers
from app.tools.local_time import get_local_time


@dataclass
class Container:
    chat: ChatProvider
    providers: Providers
    settings: Settings
    metrics: MetricsRegistry | None = None
    agents: AgentRegistry | None = None
    weather: Any = None
    local_time: Any = get_local_time
