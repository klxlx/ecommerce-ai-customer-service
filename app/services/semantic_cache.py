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
# The blocked list is deliberately narrower than before: generic policy
# questions such as "退款多久到账" or "怎么申请退货" are curated FAQ targets,
# while personal/dynamic forms (my order, tracking number, refund progress,
# concrete amounts) remain excluded.
_BLOCKED = re.compile(
    r"(我的|订单号|订单\s*[a-z0-9-]{4,}|"
    r"(?:物流|快递)单号\s*[:：]?\s*[A-Za-z0-9]{6,}|"
    r"到哪了|什么时候到|退款进度|退款到账(?:了吗|没|到哪)|"
    r"我要退|我要换|帮我退|帮我换|给我退|给我换|"
    r"投诉你们|我要投诉|12315|消协|工商|起诉|报警|"
    r"转人工|人工客服|多少钱|价格|售价|库存|有货)",
    re.IGNORECASE,
)
_POLICY_TOPIC = re.compile(
    r"(退换货|退货|换货|退款|售后|运费|保修|发票|发货|物流|"
    r"签收|破损|质量|纠纷|投诉|平台介入|平台|延保|维修|取件|"
    r"包邮|七天无理由|协商|订单信息|处理意见|证据|处理结果)",
    re.IGNORECASE,
)
_POLICY_QUESTION = re.compile(
    r"(政策|规则|流程|条件|期限|运费|凭证|要求|多久|怎么|如何|"
    r"哪里|哪些|什么|吗|可以|能否|账户|预约|执行|影响|诉讼|"
    r"证据|结果|处理|检查|拒收|协商|信息|意见)",
    re.IGNORECASE,
)
_PRODUCT_TOPIC = re.compile(
    r"(参数|配置|支持|兼容|材质|尺寸|重量|电池|续航|屏幕|相机|"
    r"接口|充电|功能|怎么用|如何使用|说明书|区别|对比|防水|"
    r"降噪|滤网|CADR|噪音|功率|容量|保温|保冷|清洗|清洁|更换|"
    r"配对|连接|蓝牙|模式|显示|温标|摄氏|华氏|适用面积|过滤|"
    r"存放|饮品|水洗|快充|指示灯|电量|等级|回连|保护膜|"
    r"首次使用|注意事项)",
    re.IGNORECASE,
)
_PRODUCT_QUESTION = re.compile(
    r"(多少|多大|多久|怎么|如何|什么|哪些|吗|可以|支持|兼容|"
    r"参数|等级|时间|寿命|工作|更换|清洗|使用|连接|存放|水洗|"
    r"显示|关闭|预约|情况)",
    re.IGNORECASE,
)
# Natural customer wording often adds a polite/demonstrative prefix. Removing
# it lets "这款耳机蓝牙版本是多少" reuse the curated FAQ entry "耳机蓝牙版本是多少".
_QUERY_PREFIX = re.compile(
    r"^(?:请问一下|请问|想问一下|想问|我想问一下|我想问|咨询一下|"
    r"这款|这个|那款|那个|我的|我买的)\s*"
)
_QUERY_ALIASES = (
    (
        re.compile(r"退款(?:多久|多长时间|什么时候)(?:能|可以)?(?:到账|到帐)"),
        "退货退款多久到账？",
    ),
    (
        re.compile(r"(?:钱|退款)(?:多久|多长时间|什么时候)(?:能|可以)?退(?:回来|回)"),
        "退货退款多久到账？",
    ),
)


def query_variants(message: str) -> list[str]:
    """Return curated lookup variants for common customer phrasing."""
    cleaned = " ".join((message or "").split()).strip()
    variants: list[str] = []
    for pattern, canonical in _QUERY_ALIASES:
        if pattern.search(cleaned):
            variants.append(canonical)
            break
    stripped = _QUERY_PREFIX.sub("", cleaned).strip()
    if stripped and stripped != cleaned:
        variants.append(stripped)
    return list(dict.fromkeys(variants))


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
    if _POLICY_TOPIC.search(text) and _POLICY_QUESTION.search(text):
        return True
    # Product parameter questions are static enough for a curated FAQ.
    return bool(
        _PRODUCT_TOPIC.search(text) and _PRODUCT_QUESTION.search(text)
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
    best_hit: CacheHit | None = None
    for query in (message, *query_variants(message)):
        try:
            vector = await embeddings.embed_query(query)
            hits = await cache_repo.search(
                session,
                vector,
                threshold=settings.semantic_cache_similarity,
                limit=settings.semantic_cache_max_results,
            )
        except Exception:
            # Cache is an optimization; never break the customer turn on failure.
            return best_hit
        if not hits:
            continue
        entry, score = hits[0]
        if best_hit is None or score > best_hit.score:
            best_hit = CacheHit(entry=entry, score=score)
        # An exact/near-exact canonical hit is good enough; do not spend an
        # extra embedding call on lower-priority variants.
        if score >= settings.semantic_cache_similarity:
            break
    if best_hit is None:
        return None
    await cache_repo.increment_hit(session, best_hit.entry)
    return best_hit


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
