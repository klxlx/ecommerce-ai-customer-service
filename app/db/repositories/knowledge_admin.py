"""Read models for administrator knowledge management."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeIngestionJob,
    KnowledgeParent,
)


async def get_document_by_source_key(
    session: AsyncSession,
    source_key: str,
) -> KnowledgeDocument | None:
    return (
        await session.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.source_key == source_key
            )
        )
    ).scalar_one_or_none()


async def has_manual_version(
    session: AsyncSession,
    document_id: int,
) -> bool:
    version = (
        await session.execute(
            select(KnowledgeDocumentVersion.id).where(
                KnowledgeDocumentVersion.document_id == document_id,
                KnowledgeDocumentVersion.extraction_strategy == "manual",
            )
        )
    ).first()
    return version is not None


async def find_legacy_manual_document(
    session: AsyncSession,
    *,
    kb_type: str,
    applicability: str,
    effective_date: date | None,
    canonical_content: str,
) -> KnowledgeDocument | None:
    """Find a pre-title-independent manual document with identical content."""
    candidates = (
        await session.execute(
            select(KnowledgeDocument, KnowledgeDocumentVersion.id)
            .join(
                KnowledgeDocumentVersion,
                KnowledgeDocumentVersion.id
                == KnowledgeDocument.current_version_id,
            )
            .where(
                KnowledgeDocument.source_key.like("manual-%"),
                KnowledgeDocument.kb_type == kb_type,
                KnowledgeDocument.applicability == applicability,
                (
                    KnowledgeDocument.effective_date.is_(None)
                    if effective_date is None
                    else KnowledgeDocument.effective_date == effective_date
                ),
                KnowledgeDocumentVersion.extraction_strategy == "manual",
                KnowledgeDocumentVersion.status == "published",
            )
            .order_by(KnowledgeDocument.id)
        )
    ).all()
    if not candidates:
        return None

    version_ids = [version_id for _, version_id in candidates]
    parents = (
        await session.execute(
            select(KnowledgeParent)
            .where(KnowledgeParent.document_version_id.in_(version_ids))
            .order_by(
                KnowledgeParent.document_version_id,
                KnowledgeParent.parent_index,
            )
        )
    ).scalars().all()
    parents_by_version: dict[int, list[KnowledgeParent]] = defaultdict(list)
    for parent in parents:
        if parent.document_version_id is not None:
            parents_by_version[parent.document_version_id].append(parent)

    for document, version_id in candidates:
        parts = []
        for parent in parents_by_version[version_id]:
            if parent.section_path:
                parts.append(f"## {parent.section_path}\n{parent.content}")
            else:
                parts.append(parent.content)
        if "\n\n".join(parts).strip() == canonical_content:
            return document
    return None


async def list_snapshots(session: AsyncSession) -> list[dict]:
    documents = (
        await session.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.deleted_at.is_(None))
            .order_by(
                KnowledgeDocument.updated_at.desc(),
                KnowledgeDocument.id.desc(),
            )
        )
    ).scalars().all()
    return await _snapshots(session, list(documents))


async def get_snapshot(
    session: AsyncSession,
    document_id: int,
) -> dict | None:
    document = await session.get(KnowledgeDocument, document_id)
    if document is None or document.deleted_at is not None:
        return None
    snapshots = await _snapshots(session, [document])
    return snapshots[0] if snapshots else None


async def get_snapshot_by_source_key(
    session: AsyncSession,
    source_key: str,
) -> dict | None:
    document = await get_document_by_source_key(session, source_key)
    if document is None:
        return None
    snapshots = await _snapshots(session, [document])
    return snapshots[0] if snapshots else None


async def get_manual_content(
    session: AsyncSession,
    document_id: int,
    version_id: int | None,
) -> str:
    if version_id is None:
        return ""
    parents = (
        await session.execute(
            select(KnowledgeParent)
            .where(
                KnowledgeParent.document_version_id == version_id,
            )
            .order_by(KnowledgeParent.parent_index)
        )
    ).scalars().all()
    parts = []
    for parent in parents:
        if parent.section_path:
            parts.append(f"## {parent.section_path}\n{parent.content}")
        else:
            parts.append(parent.content)
    return "\n\n".join(parts)


async def soft_delete_document(
    session: AsyncSession,
    document_id: int,
) -> bool:
    document = await session.get(KnowledgeDocument, document_id)
    if document is None or document.deleted_at is not None:
        return False
    document.deleted_at = datetime.now(timezone.utc)
    await session.flush()
    return True


async def _snapshots(
    session: AsyncSession,
    documents: list[KnowledgeDocument],
) -> list[dict]:
    if not documents:
        return []
    document_ids = [document.id for document in documents]
    versions = (
        await session.execute(
            select(KnowledgeDocumentVersion)
            .where(KnowledgeDocumentVersion.document_id.in_(document_ids))
            .order_by(
                KnowledgeDocumentVersion.document_id,
                KnowledgeDocumentVersion.version_number.desc(),
            )
        )
    ).scalars().all()
    jobs = (
        await session.execute(
            select(KnowledgeIngestionJob)
            .where(KnowledgeIngestionJob.document_id.in_(document_ids))
            .order_by(KnowledgeIngestionJob.id.desc())
        )
    ).scalars().all()

    versions_by_id = {version.id: version for version in versions}
    latest_versions: dict[int, KnowledgeDocumentVersion] = {}
    for version in versions:
        latest_versions.setdefault(version.document_id, version)
    latest_jobs: dict[int, KnowledgeIngestionJob] = {}
    for job in jobs:
        latest_jobs.setdefault(job.document_id, job)

    snapshots = []
    for document in documents:
        current = (
            versions_by_id.get(document.current_version_id)
            if document.current_version_id is not None
            else latest_versions.get(document.id)
        )
        latest_job = latest_jobs.get(document.id)
        origin = document.source_kind or "unknown"
        can_edit = (
            current is not None and current.extraction_strategy == "manual"
        )
        snapshots.append(
            {
                "id": document.id,
                "source_key": document.source_key,
                "title": document.title,
                "kb_type": document.kb_type,
                "source_uri": document.source_uri,
                "applicability": document.applicability,
                "effective_date": document.effective_date,
                "origin": origin,
                "can_edit": can_edit,
                "status": current.status if current is not None else "not_indexed",
                "current_version_number": (
                    current.version_number if current is not None else None
                ),
                "checksum": current.checksum if current is not None else "",
                "parent_count": current.parent_count if current is not None else 0,
                "chunk_count": current.chunk_count if current is not None else 0,
                "published_at": (
                    current.published_at if current is not None else None
                ),
                "latest_job": (
                    {
                        "id": latest_job.id,
                        "status": latest_job.status,
                        "parent_count": latest_job.parent_count,
                        "chunk_count": latest_job.chunk_count,
                        "failure_reason": latest_job.failure_reason,
                        "started_at": latest_job.started_at,
                        "finished_at": latest_job.finished_at,
                    }
                    if latest_job is not None
                    else None
                ),
                "_current_version_id": current.id if current is not None else None,
            }
        )
    return snapshots
