"""Deterministic mock providers for offline smoke tests and local demos.

These are used when ``MOCK_PROVIDERS=1`` so the full stack runs without external
model API credentials. The chat provider routes by simple keyword rules and the
embedding/rerank providers return deterministic vectors.
"""

from __future__ import annotations

import hashlib
import json
import random
import re

from app.graph.router import (
    extract_order_no,
    is_after_sale_progress_query,
    is_new_after_sale_request,
)
from app.rag.rerank import RerankHit

DIM = 1024

_ORDER_NO_RE = re.compile(r"[A-Za-z]{2,}-?\d{4,}(?:-\d+)?")
_REFERENCE_RE = re.compile(
    r"参考资料：\s*\n(?P<context>.*?)(?:\n\s*业务数据：|\Z)",
    re.DOTALL,
)
_QUESTION_RE = re.compile(r"用户问题：\s*(?P<question>.*)\s*$", re.DOTALL)


class MockChat:
    """Keyword router + canned answer generator."""

    async def complete(
        self,
        messages: list[dict],
        model: str | None = None,
    ) -> str:
        system = "\n\n".join(
            str(message.get("content") or "")
            for message in messages
            if message.get("role") == "system"
        )
        user = messages[-1]["content"] if messages else ""
        if "意图识别器" in system:
            return self._route(user)
        if "闲聊 Agent" in system:
            return self._chitchat_answer(system, user)
        return self._knowledge_answer(system, user)

    def _chitchat_answer(self, system: str, user: str) -> str:
        if "上海" in user and "天气" in user:
            return "上海今天的天气信息已经为您查到，具体请以工具结果显示为准。"
        if "几点了" in user or "时间" in user:
            return "已为您查询当前本地时间，请以工具结果为准。"
        return f"您好，我在呢。您说「{user[:40]}」，今天想聊点什么？"

    def _route(self, text: str) -> str:
        if is_after_sale_progress_query(text) or is_new_after_sale_request(text):
            action = (
                "progress"
                if is_after_sale_progress_query(text)
                else "new_request"
            )
            return json.dumps(
                {
                    "intent": "after_sale",
                    "slots": {
                        "order_no": extract_order_no(text),
                        "after_sale_action": action,
                    },
                },
                ensure_ascii=False,
            )
        if re.search(r"订单|物流|到哪|快递|配送", text):
            match = _ORDER_NO_RE.search(text)
            order_no = match.group(0) if match else ""
            return json.dumps(
                {"intent": "order", "slots": {"order_no": order_no}},
                ensure_ascii=False,
            )
        if re.search(r"价格|库存|多少钱|售价", text):
            product = _extract_product(text)
            return json.dumps(
                {"intent": "price_stock", "slots": {"product": product}},
                ensure_ascii=False,
            )
        if re.search(r"退款|退换|退货|保修|售后|运费|纠纷", text):
            return json.dumps({"intent": "policy_knowledge", "slots": {}}, ensure_ascii=False)
        if re.search(r"耳机|保温杯|充电宝|净化器|参数|使用|注意", text):
            return json.dumps({"intent": "product_knowledge", "slots": {}}, ensure_ascii=False)
        return json.dumps({"intent": "chit_chat", "slots": {}}, ensure_ascii=False)

    def _knowledge_answer(self, system: str, user: str) -> str:
        context_source = system if _REFERENCE_RE.search(system) else user
        match = _REFERENCE_RE.search(context_source)
        context = match.group("context").strip() if match else ""
        context = re.sub(r"\n{2,}", "\n", context).strip()
        if not context or context == "（无）":
            return (
                "结论：暂时没有检索到可确认的知识内容。\n"
                "下一步：您可以换个说法继续咨询，或回复「转人工」由人工客服核实。"
            )

        fragments = [
            fragment.strip()
            for fragment in re.split(r"(?<=[。！？!?；;])|\n+", context)
            if len(fragment.strip()) >= 6
        ]
        excerpt = "".join(fragments[:2]) or context
        if len(excerpt) > 160:
            excerpt = f"{excerpt[:157].rstrip('，,；;、')}..."
        return (
            f"结论：{excerpt}\n"
            "下一步：请以订单和页面信息为准；如情况不一致，"
            "可回复「转人工」进一步核实。"
        )


def _extract_product(text: str) -> str:
    for name in ("蓝牙耳机", "保温杯", "充电宝", "空气净化器"):
        if name in text:
            return name
    return ""


class MockEmbeddings:
    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        seed = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)
        rng = random.Random(seed)
        return [rng.uniform(-1.0, 1.0) for _ in range(self.dim)]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class MockReranker:
    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[RerankHit]:
        indexes = list(range(min(top_n, len(documents))))
        n = len(indexes)
        return [
            RerankHit(index=i, score=1.0 - (i / (n + 1)), document=documents[i])
            for i in indexes
        ]
