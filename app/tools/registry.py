"""Allowlisted tools exposed to the bounded read-only planner.

The registry is the single source of truth for tool names, JSON schemas,
read-only status and per-tool resilience policy. Model output is never allowed
to select a tool that is absent from this registry.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.schemas.orders import OrderFact
from app.tools.orders import get_order_and_logistics, list_owned_orders

DEFAULT_ORDER_TOOLS = "order_list,order_detail"


@dataclass(frozen=True)
class ToolPolicy:
    timeout_seconds: float = 8.0
    max_retries: int = 1
    retry_backoff_seconds: float = 0.2
    circuit_threshold: int = 3
    cooldown_seconds: float = 30.0


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[Any]]
    read_only: bool = True
    source: str = "db"
    allowed_intents: frozenset[str] = frozenset({"order"})
    policy: ToolPolicy = field(default_factory=ToolPolicy)
    serializer: Callable[[Any], Any] | None = None

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _serialize_order(fact: OrderFact | None) -> dict[str, Any] | None:
    if fact is None:
        return None
    return fact.model_dump(mode="json")


def _serialize_orders(facts: list[OrderFact]) -> list[dict[str, Any]]:
    return [fact.model_dump(mode="json") for fact in facts]


_ORDER_NO_ARGUMENT = {
    "type": "object",
    "properties": {
        "order_no": {
            "type": "string",
            "pattern": r"^[A-Za-z]{2,}-?\d{4,}(?:-\d+)?$",
            "description": "用户明确提供的订单号，例如 ORD-5001。",
        }
    },
    "required": ["order_no"],
    "additionalProperties": False,
}

ORDER_LIST_TOOL = ToolDefinition(
    name="order_list",
    description=(
        "查询当前登录用户的全部订单。只用于用户明确要求“我的订单、"
        "全部订单、订单列表”等列表查询，不接收订单号参数。"
    ),
    parameters={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    handler=list_owned_orders,
    read_only=True,
    source="orders",
    allowed_intents=frozenset({"order"}),
    policy=ToolPolicy(timeout_seconds=8.0, max_retries=1),
    serializer=_serialize_orders,
)

ORDER_DETAIL_TOOL = ToolDefinition(
    name="order_detail",
    description=(
        "查询当前登录用户单个订单的状态和物流。只能使用用户明确提供的"
        "订单号，不得编造或从历史中猜测订单号。"
    ),
    parameters=_ORDER_NO_ARGUMENT,
    handler=get_order_and_logistics,
    read_only=True,
    source="orders",
    allowed_intents=frozenset({"order"}),
    policy=ToolPolicy(timeout_seconds=8.0, max_retries=1),
    serializer=_serialize_order,
)


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    ORDER_LIST_TOOL.name: ORDER_LIST_TOOL,
    ORDER_DETAIL_TOOL.name: ORDER_DETAIL_TOOL,
}


def get_tool(name: str) -> ToolDefinition | None:
    return TOOL_REGISTRY.get(name)


def configured_tool_names(settings: Any) -> list[str]:
    """Return allowlisted tool names from settings, defaulting safely."""
    raw = getattr(settings, "fc_allowed_tools", DEFAULT_ORDER_TOOLS) or ""
    names = [name.strip() for name in raw.split(",") if name.strip()]
    if not names:
        names = [name.strip() for name in DEFAULT_ORDER_TOOLS.split(",")]
    return [name for name in names if name in TOOL_REGISTRY]


def tools_for_intent(intent: str, settings: Any) -> list[ToolDefinition]:
    names = configured_tool_names(settings)
    return [
        TOOL_REGISTRY[name]
        for name in names
        if intent in TOOL_REGISTRY[name].allowed_intents
    ]


def public_tool_specs(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    return [tool.openai_schema() for tool in tools]
