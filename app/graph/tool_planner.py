"""Bounded planner loop for the order read-only Function Calling pilot."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.core.metrics import MetricsRegistry
from app.tools.registry import public_tool_specs, tools_for_intent
from app.tools.runtime import ToolResult, execute_tool


@dataclass
class OrderToolPlan:
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    fallback_reason: str | None = None
    steps: int = 0

    def trace(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "steps": self.steps,
            "fallback_reason": self.fallback_reason,
            "tool_calls": self.tool_calls,
        }


def _assistant_tool_message(
    call: Any,
) -> dict[str, Any]:
    raw_arguments = getattr(call, "raw_arguments", "") or json.dumps(
        getattr(call, "arguments", {}), ensure_ascii=False
    )
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": raw_arguments,
                },
            }
        ],
    }


def _tool_message(result: ToolResult) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": getattr(result, "call_id", ""),
        "content": result.to_message(),
    }


def _merge_evidence(
    evidence: dict[str, Any], tool_name: str, data: Any
) -> None:
    if tool_name == "order_list":
        evidence["orders"] = data
    elif tool_name == "order_detail":
        evidence["order"] = data


async def run_order_tool_loop(
    *,
    chat: Any,
    settings: Any,
    messages: list[dict[str, Any]],
    session: Any,
    user_id: int,
    metrics: MetricsRegistry | None = None,
) -> OrderToolPlan:
    """Let the model choose at most ``fc_max_steps`` allowlisted tools."""
    tools = tools_for_intent("order", settings)
    if not tools:
        return OrderToolPlan(fallback_reason="no_tools")

    complete_with_tools = getattr(chat, "complete_with_tools", None)
    if not callable(complete_with_tools):
        return OrderToolPlan(fallback_reason="provider_no_tools")

    max_steps = max(1, min(int(getattr(settings, "fc_max_steps", 2)), 4))
    tool_schemas = public_tool_specs(tools)
    allowed_names = {tool.name for tool in tools}
    plan = OrderToolPlan()
    planner_messages = list(messages)

    for step in range(max_steps):
        plan.steps = step + 1
        response = await complete_with_tools(
            planner_messages,
            tool_schemas,
            model=getattr(settings, "fc_planner_model_name", None),
            tool_choice="auto",
        )
        if not response.tool_calls:
            if plan.tool_calls:
                break
            plan.fallback_reason = "no_tool_call"
            return plan
        if len(response.tool_calls) > 1:
            plan.fallback_reason = "multiple_tool_calls"
            return plan

        call = response.tool_calls[0]
        if call.name not in allowed_names:
            plan.fallback_reason = "unregistered_tool"
            return plan

        result = await execute_tool(
            call.name,
            session=session,
            user_id=user_id,
            arguments=call.arguments,
            metrics=metrics,
        )
        result.call_id = call.id  # type: ignore[attr-defined]
        plan.results.append(result)
        plan.tool_calls.append(
            {
                "name": call.name,
                "arguments": call.arguments,
                "status": result.status,
                "attempts": result.evidence.get("attempts"),
                "latency_ms": result.evidence.get("latency_ms"),
            }
        )

        if result.status != "ok":
            if result.status == "not_found":
                plan.fallback_reason = "not_found"
            else:
                plan.fallback_reason = f"tool_{result.status}"
            return plan

        _merge_evidence(plan.evidence, call.name, result.data)
        planner_messages.append(_assistant_tool_message(call))
        planner_messages.append(_tool_message(result))
        if step + 1 >= max_steps:
            break

    if not plan.results or not plan.evidence:
        plan.fallback_reason = plan.fallback_reason or "no_tool_result"
    return plan


__all__ = ["OrderToolPlan", "run_order_tool_loop"]
