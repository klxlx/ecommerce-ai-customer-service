"""Repository for knowledge sources, parents, and chunks."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeParent,
    KnowledgeSource,
)


async def vector_recall(
    session: AsyncSession,
    vector: list[float],
    kb_type: str,
    k: int = 12,
) -> list[tuple[KnowledgeChunk, float]]:
    """Return the ``k`` nearest child chunks by cosine similarity.

    The caller reranks these children; they must never be used directly as the
    final answer context.
    """
    distance = KnowledgeChunk.embedding.cosine_distance(vector)
    stmt = (
        select(KnowledgeChunk, distance.label("distance"))
        .join(KnowledgeParent, KnowledgeParent.id == KnowledgeChunk.parent_id)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeParent.source_id)
        .join(
            KnowledgeDocumentVersion,
            KnowledgeDocumentVersion.id == KnowledgeChunk.document_version_id,
        )
        .join(
            KnowledgeDocument,
            KnowledgeDocument.id == KnowledgeDocumentVersion.document_id,
        )
        .where(
            KnowledgeSource.kb_type == kb_type,
            KnowledgeSource.document_id == KnowledgeDocument.id,
            KnowledgeDocument.current_version_id == KnowledgeChunk.document_version_id,
            KnowledgeDocumentVersion.status == "published",
            KnowledgeDocument.deleted_at.is_(None),
            KnowledgeParent.document_version_id == KnowledgeChunk.document_version_id,
        )
        .order_by(distance)
        .limit(k)
    )
    rows = (await session.execute(stmt)).all()
    return [(chunk, 1.0 - float(distance)) for chunk, distance in rows]


async def get_parents_by_ids(
    session: AsyncSession, parent_ids: list[int]
) -> dict[int, KnowledgeParent]:
    if not parent_ids:
        return {}
    stmt = select(KnowledgeParent).where(KnowledgeParent.id.in_(parent_ids))
    parents = (await session.execute(stmt)).scalars().all()
    return {p.id: p for p in parents}


async def get_source_by_id(
    session: AsyncSession, source_id: int
) -> KnowledgeSource | None:
    return await session.get(KnowledgeSource, source_id)


async def get_sources_by_ids(
    session: AsyncSession, source_ids: list[int]
) -> dict[int, KnowledgeSource]:
    if not source_ids:
        return {}
    stmt = select(KnowledgeSource).where(KnowledgeSource.id.in_(source_ids))
    sources = (await session.execute(stmt)).scalars().all()
    return {s.id: s for s in sources}


async def get_versions_by_ids(
    session: AsyncSession, version_ids: list[int]
) -> dict[int, KnowledgeDocumentVersion]:
    if not version_ids:
        return {}
    stmt = select(KnowledgeDocumentVersion).where(
        KnowledgeDocumentVersion.id.in_(version_ids)
    )
    versions = (await session.execute(stmt)).scalars().all()
    return {version.id: version for version in versions}
