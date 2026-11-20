"""LangGraph node implementations.

Nodes read runtime dependencies (``session``, ``chat``, ``providers``) from the
state so they can be unit-tested with fakes. Dynamic facts are answered from
deterministic templates built on SQL tool results, never from the LLM.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.graph.router import (
    extract_order_no,
    is_after_sale_progress_query,
    is_explicit_handoff,
    is_greeting,
    is_new_after_sale_request,
    route,
)
from app.graph.state import (
    CHIT_CHAT_LIMIT,
    MAX_CLARIFICATION_ATTEMPTS,
    AgentState,
)
from app.prompts.answer import KNOWLEDGE_GAP_MARKER, build_answer_messages
from app.prompts.chitchat import build_chitchat_messages
from app.schemas.after_sales import AfterSaleFact
from app.schemas.catalog import AmbiguousProduct, ProductFact
from app.schemas.orders import OrderFact
from app.tools.after_sales import lookup_after_sales
from app.tools.catalog import get_product_price_stock
from app.tools.handoff import build_handoff_payload
from app.tools.knowledge import retrieve_knowledge
from app.tools.local_time import get_local_time
from app.tools.orders import get_order_and_logistics, list_owned_orders
from app.tools.weather import (
    extract_weather_city,
    is_local_time_query,
    is_weather_query,
)

REFUND_REVIEW_THRESHOLD = Decimal("500.00")

GREETING_REPLY = (
    "你好呀！我是智能客服小天，也是您的购物客服助手，可以帮您解答商品咨询、"
    "交易规则，以及查询订单进度。请问有什么可以帮您？"
)

KNOWLEDGE_GAP_REPLY = (
    "抱歉，我还暂时找不到与这个问题相关的内容，不过我已经列入我的学习内容里了。"
    "可以换个说法、补充具体的产品名或文档名称再问一次吗？"
)

CHITCHAT_FRIENDLY = (
    "您好，很高兴为您服务！您可以咨询商品信息、退换货规则，"
    "或提供订单号查询物流进度。"
)

CHITCHAT_GUIDANCE = (
    "我们还是聊聊您需要的帮助吧～您可以咨询商品、交易规则，"
    "或提供订单号查询进度。如需人工服务，请回复「转人工」。"
)
WEATHER_CITY_REQUIRED = (
    "请告诉我您想查询的城市，我再帮您看天气。"
    "您也可以继续咨询商品、订单或售后问题。"
)
WEATHER_UNAVAILABLE = (
    "抱歉，天气信息暂时查不到，请稍后再试。"
    "您可以继续咨询商品、订单或售后问题。"
)

_RESTRICTED_RETURN_STATUSES = {"待发货", "运输中"}
_MESSAGE_AMOUNT_PATTERN = re.compile(
    r"(?:退款金额|退款|退还|退款申请|金额)"
    r"[^0-9A-Za-z]{0,12}[¥￥]?\s*"
    r"(\d+(?:,\d{3})*(?:\.\d{1,2})?)"
)
_CURRENCY_AMOUNT_PATTERN = re.compile(
    r"[¥￥]\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)"
)
_PENDING_SLOT_KEYS = ("pending_intent", "pending_slots", "missing_slot")
_SLOT_ANSWER_PREFIX = re.compile(
    r"^(?:就是|是|我要|我想|想查|查询|查一下|查|看看|看一下|请问|帮我查|"
    r"请帮我查)\s*"
)
_SLOT_ANSWER_SUFFIX = re.compile(
    r"\s*(?:的价格|价格|的库存|库存|多少钱|售价|还有货吗|有货吗|可以吗|"
    r"行吗|吗|呢)\s*[。！？!?]*$"
)
_KNOWLEDGE_FOLLOW_UP_PATTERN = re.compile(
    r"^(?:那|那么|这|这个|那个|它|该)|"
    r"(?:刚才|上面|前面|上述)|"
    r"(?:需要|可以|是否|要不要|能不能).*(?:吗|呢|[？?])"
)
_ORDER_LIST_PATTERN = re.compile(
    r"(?:我的|全部|所有)订单(?:有哪些|有什么|列表|清单|都有哪些|都有什么)"
    r"|我(?:都)?(?:有|下过)(?:哪些|什么)订单"
    r"|订单(?:列表|清单)"
    r"|(?:全部|所有)订单"
)


def _build_handoff(state: AgentState, reason: str) -> dict:
    return build_handoff_payload(state, reason)


async def _complete_answer(
    state: AgentState,
    messages: list[dict],
    *,
    model: str | None = None,
    max_chars: int | None = None,
    sentinel: str | None = None,
) -> str:
    """Complete an answer, forwarding model tokens when a sink is present.

    ``sentinel`` lets the knowledge node suppress a machine-readable gap marker
    before it reaches the customer's streaming bubble.
    """
    chat = state["chat"]
    sink = state.get("stream")
    stream_factory = getattr(chat, "stream", None)
    if sink is not None and callable(stream_factory):
        stream = stream_factory(messages, model=model)
        parts: list[str] = []
        total = 0
        checking = sentinel is not None
        prefix = ""
        try:
            async for delta in stream:
                if not delta:
                    continue
                if checking:
                    prefix += delta
                    lead = prefix.lstrip()
                    if not lead:
                        continue
                    if lead.startswith(sentinel):
                        return sentinel
                    if sentinel.startswith(lead):
                        continue
                    checking = False
                    parts.append(prefix)
                    total += len(prefix)
                    await sink(prefix)
                    continue
                if max_chars is not None:
                    remaining = max_chars - total
                    if remaining <= 0:
                        break
                    delta = delta[:remaining]
                parts.append(delta)
                total += len(delta)
                await sink(delta)
            if checking and prefix:
                parts.append(prefix)
                await sink(prefix)
        finally:
            close = getattr(stream, "aclose", None)
            if callable(close):
                await close()
        answer = "".join(parts).strip()
        if answer:
            return answer

    if model:
        return (await chat.complete(messages, model=model)).strip()
    return (await chat.complete(messages)).strip()


def _knowledge_unavailable(
    state: AgentState,
    *,
    kb_type: str,
    status: str,
    parent_ids: list[int] | None = None,
) -> dict:
    knowledge_evidence: dict[str, object] = {
        "kb_type": kb_type,
        "status": status,
    }
    if parent_ids:
        knowledge_evidence["parent_ids"] = parent_ids
    tool_evidence = {
        **state.get("tool_evidence", {}),
        "knowledge": knowledge_evidence,
    }
    handoff_state = dict(state)
    handoff_state["sources"] = []
    handoff_state["tool_evidence"] = tool_evidence
    return {
        "intent": "knowledge_gap",
        "answer": KNOWLEDGE_GAP_REPLY,
        "sources": [],
        "tool_evidence": tool_evidence,
        "handoff": _build_handoff(handoff_state, "knowledge_unavailable"),
    }


def _knowledge_retrieval_query(state: AgentState) -> str:
    current = state.get("user_message", "").strip()
    if not current or len(current) > 40:
        return current
    if not _KNOWLEDGE_FOLLOW_UP_PATTERN.search(current):
        return current

    history = state.get("history", [])
    for message in reversed(history):
        if message.get("role") != "user":
            continue
        previous = str(message.get("content") or "").strip()
        if previous and previous != current:
            return f"{previous}\n{current}"
        break
    return current


def _clear_pending(slots: dict) -> dict:
    cleaned = dict(slots)
    for key in _PENDING_SLOT_KEYS:
        cleaned.pop(key, None)
    cleaned.pop("last_question", None)
    return cleaned


def _clarify(
    state: AgentState,
    question: str,
    *,
    pending_intent: str | None = None,
    pending_slots: dict | None = None,
    missing_slot: str | None = None,
) -> dict:
    attempts = state.get("clarification_attempts", 0) + 1
    slots = _clear_pending(state.get("slots", {}))
    if pending_intent and missing_slot:
        slots.update(
            {
                "pending_intent": pending_intent,
                "pending_slots": pending_slots or {},
                "missing_slot": missing_slot,
                "last_question": question,
            }
        )
    if attempts >= MAX_CLARIFICATION_ATTEMPTS:
        return {
            "intent": "handoff",
            "answer": "抱歉，多次未能确认您的需求，已为您转接人工客服，请稍候。",
            "handoff": _build_handoff(state, "多次澄清失败"),
            "clarification_attempts": attempts,
            "slots": _clear_pending(slots),
        }
    return {
        "intent": "clarification",
        "clarify_question": question,
        "clarification_attempts": attempts,
        "answer": question,
        "slots": slots,
    }


def _pending_continuation(state: AgentState) -> dict | None:
    slots = state.get("slots", {})
    pending_intent = str(slots.get("pending_intent") or "").strip()
    missing_slot = str(slots.get("missing_slot") or "").strip()
    pending_slots = slots.get("pending_slots")
    if not pending_intent or not missing_slot or not isinstance(pending_slots, dict):
        return None

    user_message = state.get("user_message", "").strip()
    resumed = _clear_pending({**slots, **pending_slots})
    if missing_slot == "order_no":
        order_no = extract_order_no(user_message)
        if not order_no:
            return None
        resumed["order_no"] = order_no
        return {"intent": pending_intent, "slots": resumed}

    if missing_slot == "product":
        product = _slot_answer_text(user_message)
        if not product:
            return None
        resumed["product"] = product
        return {"intent": pending_intent, "slots": resumed}
    return None


def _slot_answer_text(message: str) -> str:
    if is_greeting(message) or is_explicit_handoff(message):
        return ""
    cleaned = _SLOT_ANSWER_PREFIX.sub("", message.strip())
    cleaned = _SLOT_ANSWER_SUFFIX.sub("", cleaned)
    cleaned = cleaned.strip(" \t\r\n，,。！？!?~～")
    if not cleaned or len(cleaned) > 200:
        return ""
    return cleaned


def _is_order_list_query(message: str) -> bool:
    return bool(_ORDER_LIST_PATTERN.search(message))


def _parse_money(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    cleaned = str(value).strip().replace(",", "").replace("元", "").replace("¥", "")
    cleaned = cleaned.replace("￥", "").strip()
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned):
        return None
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount


def _message_refund_amount(message: str) -> Decimal | None:
    match = _MESSAGE_AMOUNT_PATTERN.search(message) or _CURRENCY_AMOUNT_PATTERN.search(
        message
    )
    return _parse_money(match.group(1)) if match else None


def _money_text(amount: Decimal) -> str:
    return f"{amount:.2f}"


async def route_node(state: AgentState) -> dict:
    user_message = state.get("user_message", "").strip()
    existing_slots = state.get("slots", {})
    pending = _pending_continuation(state)
    if pending is not None:
        return pending
    if is_greeting(user_message):
        return {"intent": "greeting", "slots": _clear_pending(existing_slots)}
    if is_explicit_handoff(user_message):
        return {"intent": "handoff", "slots": _clear_pending(existing_slots)}
    if is_after_sale_progress_query(user_message) or is_new_after_sale_request(
        user_message
    ):
        slots = _clear_pending(existing_slots)
        slots.update({
            "after_sale_action": (
                "progress"
                if is_after_sale_progress_query(user_message)
                else "new_request"
            )
        })
        order_no = extract_order_no(user_message)
        if order_no:
            slots["order_no"] = order_no
        return {"intent": "after_sale", "slots": slots}

    chat = state["chat"]
    router_agent = getattr(state.get("agents"), "router", None)
    decision = await route(
        chat,
        state.get("history", []),
        user_message,
        slots=existing_slots,
        model=getattr(router_agent, "model", None),
        system_prompt=getattr(router_agent, "system_prompt", None),
    )
    if decision is None:
        return {
            "intent": "clarification",
            "clarify_question": "抱歉，我没太理解您的意思，请您再描述一下要咨询的商品或订单问题。",
        }
    slots = _clear_pending(existing_slots)
    for key, value in decision.slots.items():
        if isinstance(value, str):
            value = value.strip()
            if not value:
                # The optimized router prompt always emits all five slots with
                # "" for missing values. Empty strings must not erase context
                # carried over from earlier turns.
                if key == "query_scope" and decision.intent == "order":
                    slots.pop("query_scope", None)
                continue
        slots[key] = value
    if decision.intent == "order":
        if _is_order_list_query(user_message):
            slots.pop("order_no", None)
            slots["query_scope"] = "list"
        elif slots.get("order_no"):
            slots.pop("query_scope", None)
    return {
        "intent": decision.intent,
        "slots": slots,
    }


async def greeting_node(state: AgentState) -> dict:
    return {"answer": GREETING_REPLY}


async def knowledge_node(state: AgentState) -> dict:
    kb_type = "product" if state.get("intent") == "product_knowledge" else "policy"
    providers = state["providers"]
    session = state["session"]
    try:
        result = await retrieve_knowledge(
            session, _knowledge_retrieval_query(state), kb_type, providers
        )
    except Exception:
        return _knowledge_unavailable(
            state,
            kb_type=kb_type,
            status="retrieval_failed",
        )
    if result.is_empty:
        return _knowledge_unavailable(
            state,
            kb_type=kb_type,
            status="not_found",
        )

    knowledge_agent = getattr(state.get("agents"), "knowledge", None)
    history = state.get("history", [])
    current = state.get("user_message", "").strip()
    if not _KNOWLEDGE_FOLLOW_UP_PATTERN.search(current):
        history = []
    messages = build_answer_messages(
        history,
        current,
        result.context,
        "",
        system_prompt=getattr(knowledge_agent, "system_prompt", None),
    )
    model = getattr(knowledge_agent, "model", None)
    answer = await _complete_answer(
        state,
        messages,
        model=model,
        sentinel=KNOWLEDGE_GAP_MARKER,
    )
    answer = answer.strip()
    if KNOWLEDGE_GAP_MARKER in answer or (
        "人工客服" in answer
        and ("暂时无法确认" in answer or "没有相关" in answer)
    ):
        return _knowledge_unavailable(
            state,
            kb_type=kb_type,
            status="insufficient_context",
            parent_ids=result.parent_ids,
        )
    if not answer:
        return _knowledge_unavailable(
            state,
            kb_type=kb_type,
            status="empty_answer",
        )
    return {
        "answer": answer,
        "sources": [s.__dict__ for s in result.sources],
        "tool_evidence": {"kb_type": kb_type, "parent_ids": result.parent_ids},
    }


async def catalog_node(state: AgentState) -> dict:
    session = state["session"]
    slots = state.get("slots", {})
    product = (slots.get("product") or slots.get("sku") or "").strip()
    if not product:
        return _clarify(
            state,
            "请问您想查询哪件商品的价格或库存呢？",
            pending_intent="price_stock",
            missing_slot="product",
        )
    try:
        fact = await get_product_price_stock(session, product)
    except Exception:
        return {
            "intent": "handoff",
            "answer": "查询商品信息时遇到问题，已为您转接人工客服。",
            "handoff": _build_handoff(state, "工具查询失败"),
        }

    if isinstance(fact, ProductFact):
        evidence = fact.model_dump(mode="json")
        answer = (
            f"商品「{fact.name}」（SKU：{fact.sku}）当前售价为 ¥{fact.price} 元，"
            f"库存 {fact.stock} 件。价格与库存实时更新，以下单页面为准。"
        )
        return {"answer": answer, "tool_evidence": {"product": evidence}}
    if isinstance(fact, AmbiguousProduct):
        names = "、".join(f"「{c.name}」" for c in fact.candidates)
        return _clarify(
            state,
            f"您想查询的是哪一件呢？{names}。请告诉我具体名称或 SKU。",
            pending_intent="price_stock",
            missing_slot="product",
        )
    return {
        "answer": f"抱歉，没有找到与「{product}」匹配的商品，请确认名称或 SKU 后重试。",
    }


async def order_node(state: AgentState) -> dict:
    session = state["session"]
    slots = state.get("slots", {})
    order_no = (slots.get("order_no") or "").strip()
    query_scope = (slots.get("query_scope") or "").strip()
    if not order_no and query_scope != "list":
        return _clarify(
            state,
            "请问您的订单号是多少？提供订单号后我可以帮您查询订单状态和物流进度。",
            pending_intent="order",
            missing_slot="order_no",
        )

    user_id = state.get("user_id")
    if query_scope == "list":
        try:
            facts = await list_owned_orders(session, user_id)
        except Exception:
            return {
                "intent": "handoff",
                "answer": "查询订单列表时遇到问题，已为您转接人工客服。",
                "handoff": _build_handoff(state, "工具查询失败"),
            }
        if not facts:
            return {
                "answer": "当前账号暂未查询到订单记录。",
                "tool_evidence": {"orders": []},
            }

        lines = [f"当前账号共有 {len(facts)} 笔订单："]
        for index, fact in enumerate(facts, start=1):
            product_names = "、".join(
                item.product_name for item in fact.items
            ) or "商品信息未记录"
            lines.append(
                f"{index}. {fact.order_no}，状态「{fact.status}」，"
                f"金额 ¥{_money_text(fact.total_amount)} 元，"
                f"下单时间 {fact.created_at:%Y-%m-%d %H:%M}，"
                f"商品：{product_names}"
            )
        return {
            "answer": "\n".join(lines),
            "tool_evidence": {
                "orders": [fact.model_dump(mode="json") for fact in facts]
            },
        }

    try:
        fact = await get_order_and_logistics(session, user_id, order_no)
    except Exception:
        return {
            "intent": "handoff",
            "answer": "查询订单时遇到问题，已为您转接人工客服。",
            "handoff": _build_handoff(state, "工具查询失败"),
        }

    if isinstance(fact, OrderFact):
        evidence = fact.model_dump(mode="json")
        lines = [
            f"您的订单 {fact.order_no} 当前状态为「{fact.status}」，"
            f"订单金额 ¥{fact.total_amount} 元。"
        ]
        if fact.logistics:
            latest = fact.logistics[-1]
            lines.append(f"最新物流：{latest.status}（{latest.location}）{latest.description}".rstrip())
        answer = "\n".join(lines)
        return {"answer": answer, "tool_evidence": {"order": evidence}}
    # Missing and unauthorized orders return the same non-enumerating message.
    return {
        "answer": "抱歉，未查询到该订单，请核对订单号后重试。",
    }


def _order_evidence(fact: OrderFact) -> dict:
    return {
        "order_no": fact.order_no,
        "status": fact.status,
        "total_amount": _money_text(fact.total_amount),
    }


def _after_sale_record(fact: AfterSaleFact) -> dict:
    evidence = fact.model_dump(mode="json", exclude={"updated_at"})
    evidence["refund_amount"] = (
        _money_text(fact.refund_amount) if fact.refund_amount is not None else None
    )
    return evidence


def _after_sale_context(
    *,
    status: str,
    order_no: str,
    order_status: str | None,
    service_type: str,
    records: list[dict] | None = None,
    handoff_required: bool = False,
) -> dict:
    return {
        "status": status,
        "order_no": order_no,
        "order_status": order_status,
        "service_type": service_type,
        "records": records or [],
        "handoff_required": handoff_required,
    }


def _after_sale_type(state: AgentState) -> str:
    slots = state.get("slots", {})
    explicit = str(slots.get("service_type") or slots.get("after_sale_type") or "")
    if explicit:
        return explicit
    message = state.get("user_message", "")
    if "换货" in message:
        return "换货"
    if "退货退款" in message or ("退货" in message and "退款" in message):
        return "退货退款"
    if "退款" in message:
        return "退款"
    if "退货" in message or "退掉" in message:
        return "退货"
    return "售后"


def _format_after_sale_fact(fact: AfterSaleFact, index: int) -> str:
    amount = (
        f"¥{_money_text(fact.refund_amount)} 元"
        if fact.refund_amount is not None
        else "未填写"
    )
    return (
        f"{index}. 类型：{fact.service_type}；状态：{fact.status}；"
        f"退款金额：{amount}；原因：{fact.reason or '未填写'}；"
        f"申请时间：{fact.created_at:%Y-%m-%d %H:%M}。"
    )


async def after_sale_node(state: AgentState) -> dict:
    session = state["session"]
    slots = state.get("slots", {})
    action = str(slots.get("after_sale_action") or "").strip()
    if action not in {"progress", "new_request"}:
        action = (
            "progress"
            if is_after_sale_progress_query(state.get("user_message", ""))
            else "new_request"
        )
    service_type = _after_sale_type(state)
    order_no = (slots.get("order_no") or "").strip()
    if not order_no:
        return _clarify(
            state,
            "请问您的订单号是多少？提供订单号后我可以帮您查询售后记录或说明申请方式。",
            pending_intent="after_sale",
            pending_slots={
                "after_sale_action": action,
                "service_type": service_type,
            },
            missing_slot="order_no",
        )

    user_id = state.get("user_id")
    evidence: dict = {}
    try:
        order = await get_order_and_logistics(session, user_id, order_no)
    except Exception:
        after_sale_context = _after_sale_context(
            status="query_error",
            order_no=order_no,
            order_status=None,
            service_type=service_type,
            handoff_required=True,
        )
        return {
            "intent": "handoff",
            "answer": "查询售后信息时遇到问题，已为您转接人工客服。",
            "handoff": _build_handoff(state, "工具查询失败"),
            "tool_evidence": evidence,
            "after_sale_context": after_sale_context,
        }

    if not isinstance(order, OrderFact):
        return {
            "answer": "抱歉，未查询到该订单，请核对订单号后重试。",
            "after_sale_context": _after_sale_context(
                status="not_available",
                order_no=order_no,
                order_status=None,
                service_type=service_type,
            ),
        }

    evidence["order"] = _order_evidence(order)
    evidence["threshold"] = {
        "applied": False,
        "amount": None,
        "limit": _money_text(REFUND_REVIEW_THRESHOLD),
        "exceeded": False,
    }

    if action == "progress":
        try:
            facts = await lookup_after_sales(session, user_id, order_no)
        except Exception:
            return {
                "intent": "handoff",
                "answer": "查询售后记录时遇到问题，已为您转接人工客服。",
                "handoff": _build_handoff(state, "工具查询失败"),
                "tool_evidence": evidence,
                "after_sale_context": _after_sale_context(
                    status="query_error",
                    order_no=order_no,
                    order_status=order.status,
                    service_type=service_type,
                    handoff_required=True,
                ),
            }
        if facts is None:
            return {
                "answer": "抱歉，未查询到该订单，请核对订单号后重试。",
                "after_sale_context": _after_sale_context(
                    status="not_available",
                    order_no=order_no,
                    order_status=None,
                    service_type=service_type,
                ),
            }

        evidence["after_sale"] = [fact.model_dump(mode="json") for fact in facts]
        records = [_after_sale_record(fact) for fact in facts]
        after_sale_context = _after_sale_context(
            status="has_records" if facts else "no_records",
            order_no=order_no,
            order_status=order.status,
            service_type=service_type,
            records=records,
        )
        if not facts:
            return {
                "answer": (
                    f"订单 {order.order_no} 当前状态为「{order.status}」。"
                    "暂未查询到售后记录，您可以在订单详情页核对售后入口；"
                    "如仍未找到，可回复「转人工」进一步核实。"
                ),
                "tool_evidence": evidence,
                "after_sale_context": after_sale_context,
            }

        lines = [
            f"订单 {order.order_no} 当前状态为「{order.status}」，"
            f"查询到 {len(facts)} 条售后记录："
        ]
        lines.extend(
            _format_after_sale_fact(fact, index)
            for index, fact in enumerate(facts, start=1)
        )
        lines.append("以上为当前系统记录，具体处理结果以售后页面为准。")
        return {
            "answer": "\n".join(lines),
            "tool_evidence": evidence,
            "after_sale_context": after_sale_context,
        }

    if order.status in _RESTRICTED_RETURN_STATUSES:
        evidence["after_sale"] = []
        return {
            "answer": (
                f"订单 {order.order_no} 当前状态为「{order.status}」。"
                "当前无法直接办理退货退款，建议您先在订单详情页查看是否可申请拦截、"
                "拒收或联系人工客服核实后续处理方式。"
            ),
            "tool_evidence": evidence,
            "after_sale_context": _after_sale_context(
                status="no_records",
                order_no=order_no,
                order_status=order.status,
                service_type=service_type,
            ),
        }

    is_refund_request = "退款" in service_type
    if not is_refund_request:
        evidence["after_sale"] = []
        return {
            "answer": (
                f"您咨询的是订单 {order.order_no} 的{service_type}。"
                "您可以在订单详情页查看售后入口并提交申请，平台会按订单和售后规则审核。"
                "当前仅提供操作指引，未实际办理售后。"
            ),
            "tool_evidence": evidence,
            "after_sale_context": _after_sale_context(
                status="no_records",
                order_no=order_no,
                order_status=order.status,
                service_type=service_type,
            ),
        }

    requested_amount = (
        _parse_money(slots.get("refund_amount"))
        or _message_refund_amount(state.get("user_message", ""))
        or order.total_amount
    )
    exceeded = requested_amount > REFUND_REVIEW_THRESHOLD
    evidence["threshold"] = {
        "applied": True,
        "amount": _money_text(requested_amount),
        "limit": _money_text(REFUND_REVIEW_THRESHOLD),
        "exceeded": exceeded,
    }
    evidence["after_sale"] = []

    if exceeded:
        reason = (
            f"新退款金额 {_money_text(requested_amount)} 元"
            f"超过 {_money_text(REFUND_REVIEW_THRESHOLD)} 元自动受理上限"
        )
        handoff_state = dict(state)
        handoff_state["tool_evidence"] = evidence
        return {
            "intent": "handoff",
            "answer": (
                f"您的退款金额 ¥{_money_text(requested_amount)} 元超过 "
                f"{_money_text(REFUND_REVIEW_THRESHOLD)} 元，"
                "需要人工客服进一步核实，已为您转接人工客服。"
            ),
            "handoff": _build_handoff(handoff_state, reason),
            "tool_evidence": evidence,
            "after_sale_context": _after_sale_context(
                status="no_records",
                order_no=order_no,
                order_status=order.status,
                service_type=service_type,
                handoff_required=True,
            ),
        }

    return {
        "answer": (
            f"您咨询的是订单 {order.order_no} 的退款。"
            "您可以在订单详情页查看退款入口并提交申请，平台会按订单和售后规则审核。"
            "当前仅提供操作指引，未实际办理退款。"
        ),
        "tool_evidence": evidence,
        "after_sale_context": _after_sale_context(
            status="no_records",
            order_no=order_no,
            order_status=order.status,
            service_type=service_type,
        ),
    }


async def chitchat_node(state: AgentState) -> dict:
    streak = state.get("chit_chat_streak", 0)
    if streak >= CHIT_CHAT_LIMIT:
        return {"answer": CHITCHAT_GUIDANCE, "chit_chat_streak": streak}

    chitchat_agent = getattr(state.get("agents"), "chitchat", None)
    # Legacy unit tests invoke this node without the runtime agent registry.
    if chitchat_agent is None:
        return {
            "answer": CHITCHAT_FRIENDLY,
            "chit_chat_streak": streak + 1,
        }

    user_message = state.get("user_message", "").strip()
    facts: dict = {}
    tool_evidence: dict = {}

    if is_weather_query(user_message):
        city = extract_weather_city(user_message)
        if not city:
            return {
                "answer": WEATHER_CITY_REQUIRED,
                "chit_chat_streak": streak + 1,
                "tool_evidence": {"weather": {"status": "missing_city"}},
            }

        weather = state.get("weather")
        if weather is None:
            return {
                "answer": WEATHER_UNAVAILABLE,
                "chit_chat_streak": streak + 1,
                "tool_evidence": {
                    "weather": {"status": "unavailable", "city": city}
                },
            }
        try:
            weather_text = await weather.query(city)
        except Exception:
            return {
                "answer": WEATHER_UNAVAILABLE,
                "chit_chat_streak": streak + 1,
                "tool_evidence": {
                    "weather": {"status": "unavailable", "city": city}
                },
            }
        weather_fact = {
            "status": "success",
            "city": city,
            "data": weather_text,
        }
        facts["weather"] = weather_fact
        tool_evidence["weather"] = weather_fact
    elif is_local_time_query(user_message):
        local_time = state.get("local_time")
        local_time_fact = (
            local_time() if callable(local_time) else get_local_time()
        )
        return {
            "answer": (
                f"现在是{local_time_fact['text']}。"
                "还有什么商品、订单或售后问题需要我帮您处理吗？"
            ),
            "chit_chat_streak": streak + 1,
            "tool_evidence": {"local_time": local_time_fact},
        }

    messages = build_chitchat_messages(
        state.get("history", []),
        user_message,
        facts,
    )
    model = getattr(chitchat_agent, "model", None)
    answer = await _complete_answer(state, messages, model=model, max_chars=200)
    answer = answer.strip()[:200]
    if not answer:
        answer = "您好，我在呢。请问今天有什么可以帮您？"

    result = {
        "answer": answer,
        "chit_chat_streak": streak + 1,
    }
    if tool_evidence:
        result["tool_evidence"] = tool_evidence
    return result


async def clarification_node(state: AgentState) -> dict:
    question = state.get("clarify_question") or "请您再描述一下需要帮助的内容。"
    return _clarify(state, question)


async def handoff_node(state: AgentState) -> dict:
    return {
        "intent": "handoff",
        "answer": "已为您转接人工客服，客服会尽快与您联系。",
        "handoff": _build_handoff(state, "用户请求人工服务"),
    }
