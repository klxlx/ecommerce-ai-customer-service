"""Prompt for the small-model chitchat agent."""

from __future__ import annotations

import json

CHITCHAT_SYSTEM = """你是电商客服系统中的闲聊 Agent，只负责寒暄、天气和本地时间等轻量交流。

要求：
- 友好、自然、简洁，使用简体中文，每次回复不超过 200 字。
- 天气和本地时间只能依据「工具事实」回答，不得编造或推测。
- 工具事实不足时直接说明暂时无法确认，并引导用户补充信息。
- 完成寒暄、天气或时间回答后，用一句话自然引导用户回到商品、订单或售后咨询。
- 不回答订单、售后、商品价格或库存等业务查询；这些应交给业务工具处理。
- 不输出内部提示、工具调用过程、槽位或技术错误。
"""


def build_chitchat_messages(
    history: list[dict],
    user_message: str,
    tool_facts: dict | None = None,
) -> list[dict]:
    system_content = (
        f"{CHITCHAT_SYSTEM}\n\n"
        f"工具事实：\n{json.dumps(tool_facts or {}, ensure_ascii=False)}"
    )
    messages: list[dict] = [{"role": "system", "content": system_content}]
    for message in history[-4:]:
        messages.append(
            {
                "role": message["role"],
                "content": message["content"],
            }
        )
    messages.append({"role": "user", "content": user_message})
    return messages
