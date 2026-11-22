"""Prompt for the bounded read-only order tool planner."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.context.builder import build_context_pack, context_engine_enabled
from app.context.models import ContextPack

TOOL_PLANNER_SYSTEM = """你是电商客服的订单查询工具规划器。
你的唯一任务是决定是否调用一个只读订单工具，以及填写参数；不要生成给顾客的最终回复。

可用工具只有：
- order_list：查询当前登录用户的全部订单。没有参数。
- order_detail：查询当前登录用户的一个订单及物流。参数 order_no 必须是用户在对话中明确提供的订单号。

规则：
1. 用户明确说“我的订单、我所有的订单、全部订单、所有订单、订单列表、查一下我的订单”时，调用 order_list。
2. 用户明确给出订单号，或明确要查某一个订单时，调用 order_detail，并原样填写订单号。
3. 用户没有给订单号，也不是在要订单列表时，不要调用工具。
4. 不得调用未提供的工具；不得调用退款、取消、改地址、建工单等写操作。
5. 一次只调用一个工具，不得猜测订单号，不得把 user_id 作为参数。
6. 参数不完整或不确定时不要调用工具，让现有澄清流程处理。"""


def build_tool_planner_pack(
    history: list[dict[str, Any]],
    user_message: str,
    *,
    slots: Mapping[str, Any] | None = None,
    settings: Any | None = None,
) -> ContextPack:
    """Build a bounded planner prompt; tools stay in the provider schema."""
    return build_context_pack(
        purpose="tool_planner",
        system_prompt=TOOL_PLANNER_SYSTEM,
        history=history,
        user_message=user_message,
        slots=slots,
        settings=settings,
    )


def build_tool_planner_messages(
    history: list[dict[str, Any]],
    user_message: str,
    *,
    slots: Mapping[str, Any] | None = None,
    settings: Any | None = None,
    context_pack: ContextPack | None = None,
) -> list[dict[str, Any]]:
    if context_pack is not None:
        return context_pack.to_messages()
    if context_engine_enabled(settings):
        return build_tool_planner_pack(
            history,
            user_message,
            slots=slots,
            settings=settings,
        ).to_messages()

    system = TOOL_PLANNER_SYSTEM
    if slots:
        system += (
            "\n\n当前已提取槽位（仅作参考，不得据此编造订单号）："
            + json.dumps(slots, ensure_ascii=False, default=str)
        )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system}
    ]
    for message in history[-6:]:
        role = message.get("role")
        content = message.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return messages
