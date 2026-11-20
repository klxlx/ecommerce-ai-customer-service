"""Deterministic guards and the LLM intent router."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from app.prompts.router import build_router_messages
from app.providers.chat import ChatProvider

VALID_INTENTS = {
    "product_knowledge",
    "policy_knowledge",
    "price_stock",
    "order",
    "after_sale",
    "chit_chat",
    "handoff",
}


class RouterDecision(BaseModel):
    intent: str
    slots: dict = Field(default_factory=dict)


_GREETING_PATTERN = re.compile(
    r"^(你好|您好|hi|hello|hey|嗨|哈喽|在吗|在么|早上好|下午好|晚上好|大家好)"
    r"[\s!！。？?~～]*$",
    re.IGNORECASE,
)

_HANDOFF_PATTERN = re.compile(r"(转人工|人工客服|人工服务|我要投诉|找人工)")
_COMPLAINT_ACTION_PATTERN = re.compile(
    r"(我要投诉|投诉你们|已经投诉|投诉过|举报|12315|消协|工商|起诉|报警|欺诈|欺骗|骗子)"
)
_COMPLAINT_POLICY_PATTERN = re.compile(
    r"(规则|政策|条件|流程|要求|怎么|如何|渠道|时效|多久|几天|是什么|在哪)"
)
_URGENT_PATTERN = re.compile(r"(紧急|加急|急事|马上处理|立刻处理|人身安全|安全问题)")
_ORDER_NO_PATTERN = re.compile(r"[A-Za-z]{2,}-?\d{4,}(?:-\d+)?")
_AFTER_SALE_TERMS_PATTERN = re.compile(r"(退款|退货|退换|退掉|售后)")
_AFTER_SALE_POLICY_PATTERN = re.compile(
    r"(规则|政策|条件|流程|要求|是什么|怎么申请|如何申请|多久|几天|"
    r"七天无理由|无理由退货)"
)
_AFTER_SALE_PROGRESS_PATTERN = re.compile(
    r"(?:退款|退货|退换|退掉|售后).{0,12}"
    r"(?:进度|到哪|到账|状态|结果|怎么样|完成|处理)"
    r"|(?:进度|到哪|到账|状态|结果|怎么样|完成|处理).{0,12}"
    r"(?:退款|退货|退换|退掉|售后)"
)
_NEW_AFTER_SALE_PATTERN = re.compile(
    r"(我要|我想|想要|申请|帮我|请帮|给我|需要|退掉|办理|发起|要求)"
)


def is_greeting(text: str) -> bool:
    """Return True for a short greeting/hello without any business content."""
    cleaned = text.strip()
    if len(cleaned) > 20:
        return False
    return bool(_GREETING_PATTERN.match(cleaned))


def is_explicit_handoff(text: str) -> bool:
    """Return True for explicit human-handoff or out-of-scope requests."""
    return bool(_HANDOFF_PATTERN.search(text))


def is_complaint_policy_query(text: str) -> bool:
    """Return True when the customer asks about complaint rules, not complains."""
    cleaned = text.strip()
    if _COMPLAINT_ACTION_PATTERN.search(cleaned):
        return False
    return "投诉" in cleaned and bool(_COMPLAINT_POLICY_PATTERN.search(cleaned))


def is_complaint_or_urgent(text: str) -> bool:
    """Return True for an actual complaint or an urgent escalation request."""
    cleaned = text.strip()
    if _COMPLAINT_ACTION_PATTERN.search(cleaned):
        return True
    if is_complaint_policy_query(cleaned):
        return False
    return bool(_URGENT_PATTERN.search(cleaned))


def _is_general_after_sale_policy(text: str) -> bool:
    return bool(
        _AFTER_SALE_TERMS_PATTERN.search(text)
        and _AFTER_SALE_POLICY_PATTERN.search(text)
    )


def is_after_sale_progress_query(text: str) -> bool:
    """Return True for a specific after-sales progress/status question."""
    cleaned = text.strip()
    if _is_general_after_sale_policy(cleaned):
        return False
    return bool(_AFTER_SALE_PROGRESS_PATTERN.search(cleaned))


def is_new_after_sale_request(text: str) -> bool:
    """Return True for a concrete first-person after-sales request."""
    cleaned = text.strip()
    if not _AFTER_SALE_TERMS_PATTERN.search(cleaned):
        return False
    if is_after_sale_progress_query(cleaned):
        return False
    if _is_general_after_sale_policy(cleaned):
        return False
    return bool(_NEW_AFTER_SALE_PATTERN.search(cleaned))


def extract_order_no(text: str) -> str:
    """Extract the first order-number-like token from user text."""
    match = _ORDER_NO_PATTERN.search(text)
    return match.group(0) if match else ""


def _extract_json(text: str) -> dict | None:
    """Best-effort extraction of a JSON object from model output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def parse_router_decision(text: str) -> RouterDecision | None:
    data = _extract_json(text)
    if not isinstance(data, dict):
        return None
    intent = str(data.get("intent", "")).strip()
    if intent not in VALID_INTENTS:
        return None
    slots = data.get("slots") or {}
    if not isinstance(slots, dict):
        slots = {}
    return RouterDecision(intent=intent, slots=slots)


async def route(
    chat: ChatProvider,
    history: list[dict],
    user_message: str,
    *,
    slots: dict | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
) -> RouterDecision | None:
    """Call the chat model to classify intent; return None on failure."""
    messages = build_router_messages(
        history,
        user_message,
        slots=slots,
        system_prompt=system_prompt,
    )
    if model:
        text = await chat.complete(messages, model=model)
    else:
        text = await chat.complete(messages)
    return parse_router_decision(text)
