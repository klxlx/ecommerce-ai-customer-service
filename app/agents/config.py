"""Static role boundaries for the three customer-service agents."""

from __future__ import annotations

from dataclasses import dataclass

from app.prompts.answer import ANSWER_SYSTEM
from app.prompts.chitchat import CHITCHAT_SYSTEM
from app.prompts.router import ROUTER_SYSTEM


@dataclass(frozen=True)
class AgentConfig:
    """One agent role with a fixed responsibility and model tier."""

    name: str
    model: str
    system_prompt: str


@dataclass(frozen=True)
class AgentRegistry:
    """The three role-specific agents used by the graph."""

    router: AgentConfig
    knowledge: AgentConfig
    chitchat: AgentConfig


def build_agent_registry(settings) -> AgentRegistry:
    """Build fixed roles from runtime model configuration."""
    return AgentRegistry(
        router=AgentConfig(
            name="意图识别 Agent",
            model=settings.router_model,
            system_prompt=ROUTER_SYSTEM,
        ),
        knowledge=AgentConfig(
            name="检索回复 Agent",
            model=settings.knowledge_model,
            system_prompt=ANSWER_SYSTEM,
        ),
        chitchat=AgentConfig(
            name="闲聊 Agent",
            model=settings.chitchat_model,
            system_prompt=CHITCHAT_SYSTEM,
        ),
    )
