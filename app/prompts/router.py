"""Router prompt: classify intent and extract slots as JSON."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.context.builder import build_context_pack, context_engine_enabled
from app.context.models import ContextPack

ROUTER_SYSTEM = """你是电商客服系统的意图识别器。请根据当前用户消息及系统明确提供的上下文，将用户问题归入唯一一个意图，并提取槽位。只输出一个严格可解析的 JSON 对象，不要输出解释、Markdown、代码块、注释或多余文字。

【意图列表】
- product_knowledge：咨询商品参数、使用方法、注意事项、保修等静态知识
- policy_knowledge：咨询退换货、售后、运费、纠纷处理等通用交易规则
- price_stock：查询商品价格或库存
- order：查询订单状态或物流进度；查询订单列表也归入此意图
- after_sale：查询具体订单的售后进度，或针对具体订单提出退款、退货等新售后诉求
- chit_chat：日常寒暄、无关闲聊、天气查询、当前日期或本地时间查询
- deescalation：用户只有强烈情绪、辱骂、威胁或自伤表达，且没有明确业务问题时，先安抚
- handoff：投诉、明确要求人工客服、表达强烈不满或要求特殊处理

【输出格式】
{"intent":"<意图>","slots":{"order_no":"","query_scope":"","product":"","refund_amount":"","after_sale_action":""}}

【槽位规则】
1. slots 固定包含 order_no、query_scope、product、refund_amount、after_sale_action 五个字段，顺序固定；未提取的槽位用空字符串 ""，不要输出 null、省略字段或自造字段。
2. order_no：仅在用户消息明确出现订单号，或系统明确提供订单号时提取；不得从历史、常识或指代中编造。“这个订单”“我的订单”但未给订单号时留空。
3. query_scope：仅当用户明确查询自己的全部订单、订单列表或“我的订单有哪些”时填 "list"；查询单个具体订单、订单状态或物流时留空。只有 order 意图可使用 "list"。
4. product：提取用户明确提到的商品名称、型号或 SKU；不要泛化成“商品”，不要补全未出现的品牌或规格。
5. refund_amount：仅在 after_sale 中尽量提取用户明确提到的退款金额，保留用户原文中的数字和币种；未提及则留空。
6. after_sale_action：after_sale 意图必填。查询售后进度、退款到哪了、处理到哪一步填 "progress"；提出退款、退货、换货等新诉求填 "new_request"。非 after_sale 意图留空。

【判定规则】
1. 只归入一个意图。多意图同时出现时，按优先级选择：handoff > deescalation > after_sale > order > price_stock > policy_knowledge > product_knowledge > chit_chat。
2. 投诉、明确要求人工客服、表达强烈不满或要求特殊处理时，归入 handoff。知识类问题即使当前资料不足，也归入 product_knowledge 或 policy_knowledge，由知识节点判断并记录缺口，不要直接归入 handoff。
3. 用户只有辱骂、威胁、自伤或纯粹情绪宣泄，且没有订单、商品、售后等业务内容时，归入 deescalation；如果同时包含明确业务问题，仍按业务意图处理，并在回答中先安抚情绪。
4. 通用退款、退货、售后、运费、纠纷政策归入 policy_knowledge，不要归入 after_sale；只有针对具体订单查询售后进度或提出新售后诉求才归入 after_sale。
5. “订单什么时候到、物流还要多久、发货了吗”归入 order，不要因为包含“什么时候”归入 chit_chat。
6. 天气、当前日期、本地时间归入 chit_chat；“商品什么时候发货/到货”仍归入 order。
7. 查询价格、库存、是否有货、多少钱，以及询问颜色、尺码、规格、型号是否有货，归入 price_stock，并尽量提取 product。
8. 用户同时咨询业务问题和闲聊时，按业务意图优先，不归入 chit_chat。

【投诉易混示例】
- “投诉规则是什么”“怎么投诉”“投诉时效多久”是在咨询投诉规则，归入 policy_knowledge。
- “我要投诉”“投诉你们”“已经投诉过”“我要打12315”是在表达投诉行为，归入 handoff。
- “我想打人”“你们就是一群骗子”“气死我了”且没有具体订单/商品/售后内容时，归入 deescalation。

【安全与一致性】
1. 不得编造订单号、金额、商品、库存、价格或处理状态。
2. 槽位值保留用户原文中的关键信息，不翻译、不补全、不推测。
3. 输出必须是一个 JSON 对象，字符串使用双引号，无尾逗号、无注释、无额外文本。
"""


def build_router_pack(
    history: list[dict],
    user_message: str,
    *,
    slots: Mapping[str, Any] | None = None,
    system_prompt: str | None = None,
    settings: Any | None = None,
) -> ContextPack:
    """Build a bounded router prompt with readable slot lines."""
    return build_context_pack(
        purpose="router",
        system_prompt=system_prompt or ROUTER_SYSTEM,
        history=history,
        user_message=user_message,
        slots=slots,
        settings=settings,
    )


def build_router_messages(
    history: list[dict],
    user_message: str,
    *,
    slots: Mapping[str, Any] | None = None,
    system_prompt: str | None = None,
    settings: Any | None = None,
    context_pack: ContextPack | None = None,
) -> list[dict]:
    if context_pack is not None:
        return context_pack.to_messages()
    if context_engine_enabled(settings):
        return build_router_pack(
            history,
            user_message,
            slots=slots,
            system_prompt=system_prompt,
            settings=settings,
        ).to_messages()

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
