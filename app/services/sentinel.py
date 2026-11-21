"""Asynchronous answer sentinel.

The sentinel is deliberately narrow: it checks whether concrete claims in a
business answer are supported by retrieved context/tool evidence, and whether
the answer makes an unsupported price/refund/return/policy promise. It is not a
general content moderator and must fail open when the guard itself fails.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

SENTINEL_INTENTS = {
    "product_knowledge",
    "policy_knowledge",
    "price_stock",
    "order",
    "after_sale",
}

SENTINEL_FALLBACK_ANSWER = (
    "抱歉，这个问题我暂时没有足够资料确认，建议您联系人工客服进一步核实。"
)

SENTINEL_SYSTEM_PROMPT = """你是电商客服回答哨兵。你只做两件事：
1. 判断回答中的事实性结论是否能被提供的“资料/工具结果”支持；
2. 判断回答是否做出没有依据的价格、退款、退货、赔付、时效或政策承诺。

不要做通用内容审核，不要评价语气、立场或是否礼貌。
寒暄、澄清问题、固定提示、操作指引（例如“请在订单详情页提交申请”）不算无依据承诺。
只有在回答给出具体事实或具体承诺、且资料中没有依据或与资料冲突时，才判为违规。
只输出一个 JSON 对象，不要输出 Markdown 代码块：
{"supported": true, "policy_risk": false, "unsupported_claims": [], "reason": ""}
"""

_JSON_OBJECT = re.compile(r"\{.*\}", re.S)


@dataclass
class SentinelVerdict:
    checked: bool
    supported: bool = True
    policy_risk: bool = False
    flagged: bool = False
    reason: str = ""
    unsupported_claims: list[str] = field(default_factory=list)
    model: str = ""
    error: str = ""

    def trace(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "supported": self.supported,
            "policy_risk": self.policy_risk,
            "flagged": self.flagged,
            "reason": self.reason,
            "unsupported_claims": self.unsupported_claims,
            "model": self.model,
            "error": self.error,
        }


def _parse_json(text: str) -> dict | None:
    candidate = (text or "").strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:\w+)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        value = json.loads(candidate)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        match = _JSON_OBJECT.search(candidate)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


async def check_answer(
    container,
    *,
    question: str,
    answer: str,
    intent: str | None,
    sources: list | None,
    tool_evidence: dict | None,
    grounding_context: str = "",
) -> SentinelVerdict:
    settings = container.settings
    model = settings.sentinel_model_name
    if not settings.sentinel_enabled:
        return SentinelVerdict(checked=False, model=model)
    if intent not in SENTINEL_INTENTS or not answer.strip():
        return SentinelVerdict(checked=False, model=model)

    context = (grounding_context or "").strip()
    if len(context) > settings.sentinel_max_context_chars:
        context = context[: settings.sentinel_max_context_chars]
    evidence = json.dumps(
        tool_evidence or {},
        ensure_ascii=False,
        default=str,
    )
    if len(evidence) > 4000:
        evidence = evidence[:4000]
    source_meta = json.dumps(sources or [], ensure_ascii=False, default=str)[:2000]

    user_prompt = (
        f"用户问题：\n{question}\n\n"
        f"客服回答：\n{answer}\n\n"
        f"检索到的资料：\n{context or '（无）'}\n\n"
        f"工具查询结果：\n{evidence or '（无）'}\n\n"
        f"来源元数据：\n{source_meta or '（无）'}"
    )
    messages = [
        {"role": "system", "content": SENTINEL_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        async with asyncio.timeout(settings.sentinel_timeout_seconds):
            raw = await container.chat.complete(messages, model=model)
    except TimeoutError:
        return SentinelVerdict(
            checked=False, model=model, error="timeout"
        )
    except Exception as exc:
        return SentinelVerdict(
            checked=False,
            model=model,
            error=type(exc).__name__,
        )

    parsed = _parse_json(raw)
    if parsed is None:
        return SentinelVerdict(
            checked=False, model=model, error="invalid_json"
        )

    supported = bool(parsed.get("supported", True))
    policy_risk = bool(parsed.get("policy_risk", False))
    claims = parsed.get("unsupported_claims") or []
    if not isinstance(claims, list):
        claims = [str(claims)]
    claims = [str(item)[:160] for item in claims[:6]]
    reason = str(parsed.get("reason") or "")[:300]
    verified = supported and not policy_risk
    if verified:
        claims = []
        reason = ""
    return SentinelVerdict(
        checked=True,
        supported=supported,
        policy_risk=policy_risk,
        flagged=not verified,
        reason=reason,
        unsupported_claims=claims,
        model=model,
    )
