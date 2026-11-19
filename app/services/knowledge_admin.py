"""Administrator knowledge ingestion built on the versioned RAG indexer."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import knowledge_admin as knowledge_repo
from app.graph.container import Container
from app.rag.chunking import split_parents
from app.rag.indexer import IndexResult, index_extracted_documents
from app.rag.pdf_extraction import ExtractedDocument, ExtractedParent
from app.schemas.knowledge import KnowledgeIngestionOut, KnowledgeUpsert
from app.services.knowledge_metadata import (
    KnowledgeMetadata,
    detect_knowledge_metadata,
)


class KnowledgeConflictError(Exception):
    """Raised when a managed PDF document is targeted by a manual update."""


@dataclass(frozen=True)
class KnowledgeUpsertResult:
    source_key: str
    ingestion: KnowledgeIngestionOut
    snapshot: dict | None


async def upsert_manual_knowledge(
    session: AsyncSession,
    container: Container,
    payload: KnowledgeUpsert,
) -> KnowledgeUpsertResult:
    metadata = detect_knowledge_metadata(
        payload.content,
        source_key=payload.source_key,
    )
    stable_source_key = _manual_source_key(payload, metadata)
    source_key = payload.source_key or stable_source_key
    lock_id = _advisory_lock_id(source_key)
    engine = session.bind
    if engine is None:
        raise RuntimeError("knowledge ingestion session has no async engine")

    # The indexer commits while publishing a version, so a transaction-level
    # lock would be released too early. Keep a session-level lock on a
    # dedicated connection for the complete read/index/snapshot operation.
    async with engine.connect() as lock_connection:
        await lock_connection.execute(
            text("SELECT pg_advisory_lock(:lock_id)"),
            {"lock_id": lock_id},
        )
        await lock_connection.commit()
        try:
            existing = await knowledge_repo.get_document_by_source_key(
                session, source_key
            )
            if existing is None and payload.source_key is None:
                existing = await knowledge_repo.find_legacy_manual_document(
                    session,
                    kb_type=payload.kb_type,
                    applicability=metadata.applicability,
                    effective_date=metadata.effective_date,
                    canonical_content=_canonical_manual_content(payload.content),
                )
                if existing is not None:
                    source_key = existing.source_key
            if existing is not None:
                if not await knowledge_repo.has_manual_version(
                    session, existing.id
                ):
                    raise KnowledgeConflictError(
                        "正式 PDF 资料由注册表维护，不能通过手工知识接口修改"
                    )

            legacy_checksum_title = (
                existing.title
                if (
                    existing is not None
                    and source_key.startswith("manual-")
                    and source_key != stable_source_key
                )
                else None
            )
            document = _to_extracted_document(
                payload,
                source_key,
                metadata,
                legacy_checksum_title=legacy_checksum_title,
            )
            result = await index_extracted_documents(
                session,
                container.providers.embeddings,
                [document],
                embedding_model=container.settings.embedding_model,
            )
            ingestion = _ingestion_result(result)
            snapshot = await knowledge_repo.get_snapshot_by_source_key(
                session, source_key
            )
            return KnowledgeUpsertResult(
                source_key=source_key,
                ingestion=ingestion,
                snapshot=snapshot,
            )
        finally:
            await lock_connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": lock_id},
            )
            await lock_connection.commit()


def _manual_source_key(
    payload: KnowledgeUpsert,
    metadata: KnowledgeMetadata,
) -> str:
    canonical = json.dumps(
        _stable_canonical_fields(payload, metadata),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"manual-{digest}"


def _stable_canonical_fields(
    payload: KnowledgeUpsert,
    metadata: KnowledgeMetadata,
) -> dict:
    return {
        "kb_type": payload.kb_type,
        "applicability": metadata.applicability,
        "effective_date": (
            metadata.effective_date.isoformat()
            if metadata.effective_date is not None
            else None
        ),
        "content": payload.content.strip(),
    }


def _legacy_canonical_fields(
    payload: KnowledgeUpsert,
    metadata: KnowledgeMetadata,
    *,
    title: str,
) -> dict:
    return {
        "title": title,
        **_stable_canonical_fields(payload, metadata),
    }


def _canonical_manual_content(content: str) -> str:
    parts = []
    for block in split_parents(content):
        if block.heading:
            parts.append(f"## {block.heading}\n{block.content}")
        else:
            parts.append(block.content)
    return "\n\n".join(parts).strip()


def _advisory_lock_id(source_key: str) -> int:
    digest = hashlib.sha256(source_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _to_extracted_document(
    payload: KnowledgeUpsert,
    source_key: str,
    metadata: KnowledgeMetadata,
    *,
    legacy_checksum_title: str | None,
) -> ExtractedDocument:
    content = payload.content.strip()
    parents = tuple(
        ExtractedParent(
            index=block.index,
            content=block.content,
            section_path=block.heading,
            page_start=None,
            page_end=None,
        )
        for block in split_parents(content)
    )
    checksum_fields = (
        _stable_canonical_fields(payload, metadata)
        if legacy_checksum_title is None
        else _legacy_canonical_fields(
            payload,
            metadata,
            title=legacy_checksum_title,
        )
    )
    checksum_source = json.dumps(
        checksum_fields,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ExtractedDocument(
        source_id=source_key,
        file_name=f"manual://{source_key}",
        title=payload.title,
        kb_type=payload.kb_type,
        applicability=metadata.applicability,
        effective_date=(
            metadata.effective_date.isoformat()
            if metadata.effective_date is not None
            else None
        ),
        checksum=hashlib.sha256(checksum_source.encode("utf-8")).hexdigest(),
        page_count=0,
        extraction_strategy="manual",
        parents=parents,
        source_kind=payload.source_kind or "manual",
        original_filename=(payload.original_filename or "").strip()[:500],
    )


def _ingestion_result(result: IndexResult) -> KnowledgeIngestionOut:
    failure_reason = _safe_failure_reason(result.errors)
    if failure_reason:
        status = "failed"
    elif result.documents_skipped:
        status = "unchanged"
    else:
        status = "succeeded"
    return KnowledgeIngestionOut(
        status=status,
        success_count=result.documents_indexed,
        documents_seen=result.documents_seen,
        documents_indexed=result.documents_indexed,
        documents_skipped=result.documents_skipped,
        versions_published=result.versions_published,
        chunks_indexed=result.chunks_indexed,
        failure_reason=failure_reason,
    )


def _safe_failure_reason(errors: list[str]) -> str:
    if not errors:
        return ""
    reason = errors[0]
    if ": " in reason:
        reason = reason.split(": ", 1)[1]
    return reason.strip()[:500]
