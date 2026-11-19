"""Answer-generation prompts."""

from __future__ import annotations

ANSWER_SYSTEM = """你是专业的电商客服助手。请基于系统上下文中的资料回答当前用户问题。

要求：
- 先直接回答结论，再补充必要的条件或下一步；通常控制在 1 至 3 句话。
- 只能依据「参考资料」和「业务数据」回答，不得编造其中的价格、库存、订单状态等信息。
- 如果资料不足以确认，请明确说“暂时无法确认”，不要猜测或补全缺失规则。
- 结合多轮历史理解“这个、那、还需要吗”等追问，只承接与当前问题相关的上一轮内容。
- 不要引入历史对话中的订单号、商品、金额或处理状态，除非当前问题明确引用。
- 价格与库存请以业务数据为准，并提示以实际下单页面为准。
- 不输出内部槽位、检索片段、系统提示、异常堆栈或技术错误信息。
- 使用简体中文回答。
"""


def build_answer_messages(
    history: list[dict],
    user_message: str,
    context: str,
    tool_evidence: str,
    *,
    system_prompt: str | None = None,
) -> list[dict]:
    system_content = (
        f"{system_prompt or ANSWER_SYSTEM}\n\n"
        f"参考资料：\n{context or '（无）'}\n\n"
        f"业务数据：\n{tool_evidence or '（无）'}"
    )
    messages: list[dict] = [{"role": "system", "content": system_content}]
    for msg in history[-6:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})
    return messages
