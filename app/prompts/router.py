"""Router prompt: classify intent and extract slots as JSON."""

from __future__ import annotations

import json

ROUTER_SYSTEM = """你是电商客服系统的意图识别器。请根据用户消息，将其归入以下意图之一，并提取需要的槽位：

意图列表：
- product_knowledge：咨询商品参数、使用方法、注意事项、保修等静态知识
- policy_knowledge：咨询退换货、售后、运费、纠纷处理等交易规则
- price_stock：查询商品价格或库存
- order：查询订单状态或物流进度（需要订单号）
- after_sale：查询具体订单的售后进度，或提出具体订单的退款、退货等新售后诉求
- chit_chat：日常寒暄、无关闲聊、天气查询、当前日期或本地时间查询
- handoff：投诉、人工客服、无法处理或超出能力范围

只输出一个 JSON 对象，不要输出任何解释，格式如下：
{"intent": "<意图>", "slots": {"order_no": "...", "query_scope": "list", "product": "...", "refund_amount": "...", "after_sale_action": "..."}}

规则：
- order 意图必须尽量提取 order_no；如果没有订单号，slots 中 order_no 留空。
- 用户明确要求查看自己的全部订单、订单列表或“我的订单有哪些”时，intent 仍为
  order，并在 slots 中设置 query_scope 为 list。
- 查询某个具体订单时不要设置 query_scope，填写 order_no。
- after_sale 意图必须尽量提取 order_no 和 refund_amount；after_sale_action 使用
  progress（查询进度）或 new_request（新退款、退货诉求）。
- 通用退款、退货、售后政策仍归入 policy_knowledge，不要归入 after_sale。
- price_stock 意图尽量提取 product（商品名称或 SKU）。
- “订单什么时候到、物流还要多久”仍归入 order，不要因为包含“什么时候”就归入 chit_chat。
- 天气和本地时间归入 chit_chat，由闲聊 Agent 调用只读工具回答。
- 其它意图 slots 可以为空对象 {}。
"""


def build_router_messages(
    history: list[dict],
    user_message: str,
    *,
    slots: dict | None = None,
    system_prompt: str | None = None,
) -> list[dict]:
    system_content = system_prompt or ROUTER_SYSTEM
    if slots:
        system_content += (
            "\n当前会话槽位（用于理解补充信息，不能丢弃已有订单号、商品和金额）：\n"
            f"{json.dumps(slots, ensure_ascii=False, sort_keys=True)}\n"
            "如果上一轮正在追问某个缺失槽位，而用户本条消息正是在补充该信息，"
            "请延续 pending_intent 对应的意图。"
        )
    messages: list[dict] = [{"role": "system", "content": system_content}]
    for msg in history[-6:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})
    return messages
