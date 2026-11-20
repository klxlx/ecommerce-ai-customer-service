"""Curated semantic FAQ cache backed by Postgres + pgvector."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import SemanticCache
from app.db.repositories import semantic_cache as cache_repo
from app.services.handoff import normalize_question

# Dynamic/transactional questions must never be answered from a static FAQ.
_BLOCKED = re.compile(
    r"(我的|订单号|订单\s*[a-z0-9-]{4,}|物流|快递|到哪|什么时候到|"
    r"退款进度|退款到账|我要退|我要换|帮我退|申请退|投诉|人工|"
    r"多少钱|价格|售价|库存|有货)",
    re.IGNORECASE,
)
_POLICY_FAQ = re.compile(
    r"(退换货|退货|换货|退款|售后).*(政策|规则|流程|条件|期限|运费|凭证)|"
    r"七天无理由|保修|发票|运费|发货时间|支付方式|优惠券"
)


@dataclass
class CacheHit:
    entry: SemanticCache
    score: float


def should_lookup(message: str) -> bool:
    """Only try the FAQ cache for non-personal, non-dynamic questions."""
    text = " ".join((message or "").split())
    if len(text) < 4 or len(text) > 160:
        return False
    if _BLOCKED.search(text):
        return False
    if _POLICY_FAQ.search(text):
        return True
    # Product parameter questions are static enough for a curated FAQ.
    return bool(
        re.search(
            r"(参数|配置|支持|兼容|材质|尺寸|重量|电池|续航|屏幕|相机|"
            r"接口|充电|功能|怎么用|如何使用|说明书|区别|对比)",
            text,
        )
    )


async def lookup(
    session: AsyncSession,
    message: str,
    embeddings,
    settings: Settings,
) -> CacheHit | None:
    if not settings.semantic_cache_enabled or embeddings is None:
        return None
    if not should_lookup(message):
        return None
    try:
        vector = await embeddings.embed_query(message)
        hits = await cache_repo.search(
            session,
            vector,
            threshold=settings.semantic_cache_similarity,
            limit=settings.semantic_cache_max_results,
        )
    except Exception:
        # Cache is an optimization; never break the customer turn on failure.
        return None
    if not hits:
        return None
    entry, score = hits[0]
    await cache_repo.increment_hit(session, entry)
    return CacheHit(entry=entry, score=score)


def parse_seed_entries(raw: list[dict]) -> list[dict]:
    entries: list[dict] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index + 1} 条不是对象")
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        kb_type = str(item.get("kb_type") or "policy").strip()
        if not question or not answer:
            raise ValueError(f"第 {index + 1} 条缺少 question 或 answer")
        if kb_type not in {"policy", "product"}:
            raise ValueError(f"第 {index + 1} 条 kb_type 必须是 policy/product")
        sources = item.get("sources") or []
        if not isinstance(sources, list):
            raise ValueError(f"第 {index + 1} 条 sources 必须是数组")
        expires_at = None
        if item.get("expires_at"):
            expires_at = datetime.fromisoformat(str(item["expires_at"]))
        entries.append(
            {
                "question": question,
                "answer": answer,
                "kb_type": kb_type,
                "sources": sources,
                "expires_at": expires_at,
            }
        )
    return entries


async def import_entries(
    session: AsyncSession,
    entries: list[dict],
    embeddings,
) -> int:
    """Embed and upsert curated FAQ entries. Caller commits the transaction."""
    if not entries:
        return 0
    vectors = await embeddings.embed_documents(
        [entry["question"] for entry in entries]
    )
    imported = 0
    for entry, vector in zip(entries, vectors, strict=True):
        await cache_repo.upsert(
            session,
            question=entry["question"],
            question_norm=normalize_question(entry["question"]),
            kb_type=entry["kb_type"],
            answer=entry["answer"],
            sources=entry["sources"],
            embedding=vector,
            expires_at=entry.get("expires_at"),
        )
        imported += 1
    return imported


async def clear(session: AsyncSession) -> int:
    return await cache_repo.clear_all(session)


async def count(session: AsyncSession) -> int:
    return await cache_repo.count(session)
