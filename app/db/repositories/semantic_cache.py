"""Repository for the curated semantic FAQ cache."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SemanticCache


async def search(
    session: AsyncSession,
    embedding: list[float],
    *,
    threshold: float,
    limit: int = 3,
) -> list[tuple[SemanticCache, float]]:
    now = datetime.now(timezone.utc)
    distance = SemanticCache.embedding.cosine_distance(embedding)
    result = await session.execute(
        select(SemanticCache, distance.label("distance"))
        .where(
            or_(
                SemanticCache.expires_at.is_(None),
                SemanticCache.expires_at > now,
            )
        )
        .order_by(distance)
        .limit(max(1, limit))
    )
    hits: list[tuple[SemanticCache, float]] = []
    for entry, raw_distance in result.all():
        score = 1.0 - float(raw_distance)
        if score >= threshold:
            hits.append((entry, score))
    return hits


async def get_by_key(
    session: AsyncSession,
    question_norm: str,
    kb_type: str,
) -> SemanticCache | None:
    result = await session.execute(
        select(SemanticCache).where(
            SemanticCache.question_norm == question_norm,
            SemanticCache.kb_type == kb_type,
        )
    )
    return result.scalar_one_or_none()


async def upsert(
    session: AsyncSession,
    *,
    question: str,
    question_norm: str,
    kb_type: str,
    answer: str,
    sources: list,
    embedding: list[float],
    expires_at: datetime | None = None,
) -> SemanticCache:
    entry = await get_by_key(session, question_norm, kb_type)
    if entry is None:
        entry = SemanticCache(
            question=question,
            question_norm=question_norm,
            kb_type=kb_type,
            answer=answer,
            sources=sources,
            embedding=embedding,
            expires_at=expires_at,
        )
        session.add(entry)
    else:
        entry.question = question
        entry.answer = answer
        entry.sources = sources
        entry.embedding = embedding
        entry.expires_at = expires_at
    await session.flush()
    return entry


async def increment_hit(session: AsyncSession, entry: SemanticCache) -> None:
    entry.hit_count = (entry.hit_count or 0) + 1
    await session.flush()


async def clear_all(session: AsyncSession) -> int:
    result = await session.execute(delete(SemanticCache))
    await session.flush()
    return int(result.rowcount or 0)


async def count(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(SemanticCache.id)))
    return int(result.scalar_one())
