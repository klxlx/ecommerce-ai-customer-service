"""Knowledge indexing: manifest -> sources -> parents -> embedded children."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    EMBEDDING_DIMENSION,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeIngestionJob,
    KnowledgeParent,
    KnowledgeSource,
)
from app.rag.chunking import (
    assert_no_dynamic_fields,
    split_children,
    split_parents,
    split_parent_text,
)
from app.rag.embeddings import EmbeddingProvider
from app.rag.markdown_extraction import (
    extract_markdown,
    load_markdown_registry,
)
from app.rag.pdf_extraction import (
    ExtractedDocument,
    extract_pdf,
    load_pdf_sources,
)


@dataclass
class IndexResult:
    sources_seen: int = 0
    sources_indexed: int = 0
    sources_skipped: int = 0
    chunks_indexed: int = 0
    documents_seen: int = 0
    documents_indexed: int = 0
    documents_skipped: int = 0
    versions_published: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _PreparedDocument:
    parents: tuple[tuple[object, list[str]], ...]
    texts: tuple[str, ...]


async def index_extracted_documents(
    session: AsyncSession,
    provider: EmbeddingProvider,
    documents: list[ExtractedDocument],
    *,
    embedding_model: str,
    chunk_size: int = 400,
    overlap: int = 80,
) -> IndexResult:
    """Version and publish already-extracted PDF documents.

    All text and vector validation happens before new parent/chunk rows are
    written. A failure marks the attempted version and job as failed, while the
    previously published version and its rows remain intact.
    """
    result = IndexResult()
    for document in documents:
        result.documents_seen += 1
        try:
            prepared = _prepare_document(
                document,
                chunk_size=chunk_size,
                overlap=overlap,
            )
        except ValueError as exc:
            result.errors.append(f"{document.source_id}: {exc}")
            continue

        existing_version = await _existing_version(
            session,
            document,
            embedding_model=embedding_model,
            chunk_size=chunk_size,
            overlap=overlap,
        )
        if (
            existing_version is not None
            and existing_version.status in {"published", "retired"}
        ):
            if existing_version.status == "published":
                await _reuse_published_version(
                    session,
                    document.source_id,
                    document,
                )
            else:
                await _restore_deleted_version(
                    session,
                    document.source_id,
                    existing_version,
                    document,
                )
            result.documents_skipped += 1
            continue

        try:
            knowledge_document, version, job = await _start_ingestion(
                session,
                document,
                embedding_model=embedding_model,
                chunk_size=chunk_size,
                overlap=overlap,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            result.errors.append(f"{document.source_id}: {_error_reason(exc)}")
            continue

        try:
            vectors = await provider.embed_documents(list(prepared.texts))
            _validate_embeddings(vectors, expected_count=len(prepared.texts))
            await _publish_version(
                session,
                document,
                knowledge_document,
                version,
                job,
                prepared,
                vectors,
                embedding_model=embedding_model,
                chunk_size=chunk_size,
                overlap=overlap,
            )
            await session.commit()
        except Exception as exc:
            reason = _error_reason(exc)
            await session.rollback()
            await _mark_failed(session, version.id, job.id, reason)
            result.errors.append(f"{document.source_id}: {reason}")
            continue

        result.documents_indexed += 1
        result.versions_published += 1
        result.chunks_indexed += len(prepared.texts)

    return result


async def index_registered_pdfs(
    session: AsyncSession,
    provider: EmbeddingProvider,
    registry_path: str | Path,
    *,
    project_root: str | Path,
    embedding_model: str,
    chunk_size: int = 400,
    overlap: int = 80,
) -> IndexResult:
    """Extract and version all PDFs declared in the official registry."""
    registry_path = Path(registry_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    sources = load_pdf_sources(registry_path)
    pdf_root = Path(project_root) / registry["pdf_root"]
    result = IndexResult(documents_seen=len(sources))
    documents: list[ExtractedDocument] = []
    for source in sources:
        try:
            documents.append(extract_pdf(source, pdf_root))
        except Exception as exc:
            result.errors.append(f"{source.source_id}: {_error_reason(exc)}")

    ingestion = await index_extracted_documents(
        session,
        provider,
        documents,
        embedding_model=embedding_model,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    result.documents_indexed = ingestion.documents_indexed
    result.documents_skipped = ingestion.documents_skipped
    result.versions_published = ingestion.versions_published
    result.chunks_indexed = ingestion.chunks_indexed
    result.errors.extend(ingestion.errors)
    return result


async def index_registered_markdowns(
    session: AsyncSession,
    provider: EmbeddingProvider,
    registry_path: str | Path,
    *,
    project_root: str | Path,
    embedding_model: str,
    chunk_size: int = 400,
    overlap: int = 80,
) -> IndexResult:
    """Extract and version the small Markdown bundle used for demo startup."""
    registry_path = Path(registry_path)
    registry = load_markdown_registry(registry_path)
    knowledge_root = Path(project_root) / registry.root
    result = IndexResult(documents_seen=len(registry.sources))
    documents: list[ExtractedDocument] = []
    for source in registry.sources:
        try:
            documents.append(extract_markdown(source, knowledge_root))
        except Exception as exc:
            result.errors.append(f"{source.source_id}: {_error_reason(exc)}")

    ingestion = await index_extracted_documents(
        session,
        provider,
        documents,
        embedding_model=embedding_model,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    result.documents_indexed = ingestion.documents_indexed
    result.documents_skipped = ingestion.documents_skipped
    result.versions_published = ingestion.versions_published
    result.chunks_indexed = ingestion.chunks_indexed
    result.errors.extend(ingestion.errors)
    return result


def _prepare_document(
    document: ExtractedDocument,
    *,
    chunk_size: int,
    overlap: int,
) -> _PreparedDocument:
    if not document.parents:
        raise ValueError("document has no extractable parents")
    parents: list[tuple[object, list[str]]] = []
    texts: list[str] = []
    for parent in document.parents:
        for content in split_parent_text(parent.content):
            children = split_children(
                content,
                chunk_size=chunk_size,
                overlap=overlap,
            )
            if not children:
                continue
            for text in children:
                assert_no_dynamic_fields(text)
            bounded_parent = replace(
                parent,
                index=len(parents),
                content=content,
            )
            parents.append((bounded_parent, children))
            texts.extend(children)
    if not parents:
        raise ValueError("document has no non-empty child chunks")
    return _PreparedDocument(parents=tuple(parents), texts=tuple(texts))


async def _existing_version(
    session: AsyncSession,
    document: ExtractedDocument,
    *,
    embedding_model: str,
    chunk_size: int,
    overlap: int,
) -> KnowledgeDocumentVersion | None:
    knowledge_document = (
        await session.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.source_key == document.source_id
            )
        )
    ).scalar_one_or_none()
    if knowledge_document is None:
        return None
    return (
        await session.execute(
            select(KnowledgeDocumentVersion).where(
                KnowledgeDocumentVersion.document_id == knowledge_document.id,
                KnowledgeDocumentVersion.checksum == document.checksum,
                KnowledgeDocumentVersion.chunk_size == chunk_size,
                KnowledgeDocumentVersion.overlap == overlap,
                KnowledgeDocumentVersion.embedding_model == embedding_model,
            )
        )
    ).scalar_one_or_none()


async def _start_ingestion(
    session: AsyncSession,
    document: ExtractedDocument,
    *,
    embedding_model: str,
    chunk_size: int,
    overlap: int,
) -> tuple[KnowledgeDocument, KnowledgeDocumentVersion, KnowledgeIngestionJob]:
    knowledge_document = (
        await session.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.source_key == document.source_id
            )
        )
    ).scalar_one_or_none()
    if knowledge_document is None:
        knowledge_document = KnowledgeDocument(
            source_key=document.source_id,
            title=document.title,
            kb_type=document.kb_type,
            source_uri=document.file_name,
            applicability=document.applicability,
            effective_date=_parse_date(document.effective_date),
            source_kind=document.source_kind,
            original_filename=document.original_filename,
        )
        session.add(knowledge_document)
        await session.flush()
    else:
        knowledge_document.title = document.title
        knowledge_document.kb_type = document.kb_type
        knowledge_document.source_uri = document.file_name
        knowledge_document.applicability = document.applicability
        knowledge_document.effective_date = _parse_date(document.effective_date)
        knowledge_document.source_kind = document.source_kind
        knowledge_document.original_filename = document.original_filename

    version = (
        await session.execute(
            select(KnowledgeDocumentVersion).where(
                KnowledgeDocumentVersion.document_id == knowledge_document.id,
                KnowledgeDocumentVersion.checksum == document.checksum,
                KnowledgeDocumentVersion.chunk_size == chunk_size,
                KnowledgeDocumentVersion.overlap == overlap,
                KnowledgeDocumentVersion.embedding_model == embedding_model,
            )
        )
    ).scalar_one_or_none()
    if version is None:
        max_version = await session.scalar(
            select(func.max(KnowledgeDocumentVersion.version_number)).where(
                KnowledgeDocumentVersion.document_id == knowledge_document.id
            )
        )
        version = KnowledgeDocumentVersion(
            document_id=knowledge_document.id,
            version_number=(max_version or 0) + 1,
            checksum=document.checksum,
            chunk_size=chunk_size,
            overlap=overlap,
            status="draft",
            pdf_file=document.file_name,
            page_count=document.page_count,
            extraction_strategy=document.extraction_strategy,
            embedding_model=embedding_model,
        )
        session.add(version)
        await session.flush()
    else:
        if version.status in {"published", "retired"}:
            raise ValueError(
                "published or retired knowledge versions cannot be re-ingested"
            )
        await session.execute(
            delete(KnowledgeParent).where(
                KnowledgeParent.document_version_id == version.id
            )
        )
        version.status = "draft"
        version.pdf_file = document.file_name
        version.page_count = document.page_count
        version.extraction_strategy = document.extraction_strategy
        version.parent_count = 0
        version.chunk_count = 0
        version.failure_reason = ""
        version.published_at = None

    job = KnowledgeIngestionJob(
        document_id=knowledge_document.id,
        document_version_id=version.id,
        status="running",
    )
    session.add(job)
    await session.flush()
    return knowledge_document, version, job


def _validate_embeddings(
    vectors: list[list[float]],
    *,
    expected_count: int,
) -> None:
    if len(vectors) != expected_count:
        raise ValueError(
            f"embedding count mismatch: {len(vectors)} != {expected_count}"
        )
    for vector in vectors:
        if len(vector) != EMBEDDING_DIMENSION:
            raise ValueError(
                f"embedding dimension {len(vector)} != {EMBEDDING_DIMENSION}"
            )


async def _publish_version(
    session: AsyncSession,
    document: ExtractedDocument,
    knowledge_document: KnowledgeDocument,
    version: KnowledgeDocumentVersion,
    job: KnowledgeIngestionJob,
    prepared: _PreparedDocument,
    vectors: list[list[float]],
    *,
    embedding_model: str,
    chunk_size: int,
    overlap: int,
) -> None:
    del embedding_model, chunk_size, overlap
    source = (
        await session.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.source_uri == document.file_name
            )
        )
    ).scalar_one_or_none()
    if source is None:
        source = KnowledgeSource(
            name=document.title,
            source_uri=document.file_name,
            kb_type=document.kb_type,
            checksum=document.checksum,
            document_id=knowledge_document.id,
        )
        session.add(source)
        await session.flush()
    else:
        source.name = document.title
        source.kb_type = document.kb_type
        source.checksum = document.checksum
        source.document_id = knowledge_document.id

    chunk_counter = 0
    for extracted_parent, child_texts in prepared.parents:
        parent = KnowledgeParent(
            source_id=source.id,
            parent_index=extracted_parent.index,
            content=extracted_parent.content,
            meta={
                "title": document.title,
                "file_name": document.file_name,
                "checksum": document.checksum,
            },
            document_version_id=version.id,
            page_start=extracted_parent.page_start,
            page_end=extracted_parent.page_end,
            section_path=extracted_parent.section_path,
        )
        session.add(parent)
        await session.flush()
        for text in child_texts:
            session.add(
                KnowledgeChunk(
                    parent_id=parent.id,
                    chunk_index=chunk_counter,
                    content=text,
                    embedding=vectors[chunk_counter],
                    document_version_id=version.id,
                    page_start=extracted_parent.page_start,
                    page_end=extracted_parent.page_end,
                    section_path=extracted_parent.section_path,
                )
            )
            chunk_counter += 1

    await session.execute(
        update(KnowledgeDocumentVersion)
        .where(
            KnowledgeDocumentVersion.document_id == knowledge_document.id,
            KnowledgeDocumentVersion.id != version.id,
            KnowledgeDocumentVersion.status == "published",
        )
        .values(status="retired")
    )
    now = datetime.now(timezone.utc)
    version.status = "published"
    version.parent_count = len(prepared.parents)
    version.chunk_count = chunk_counter
    version.failure_reason = ""
    version.published_at = now
    knowledge_document.current_version_id = version.id
    knowledge_document.deleted_at = None
    job.status = "succeeded"
    job.parent_count = len(prepared.parents)
    job.chunk_count = chunk_counter
    job.failure_reason = ""
    job.finished_at = now
    await session.flush()


async def _reuse_published_version(
    session: AsyncSession,
    source_key: str,
    document: ExtractedDocument,
) -> None:
    knowledge_document = (
        await session.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.source_key == source_key
            )
        )
    ).scalar_one_or_none()
    if knowledge_document is None:
        return
    _sync_document_metadata(knowledge_document, document)
    await _sync_knowledge_source(session, knowledge_document, document)
    knowledge_document.deleted_at = None
    await session.commit()


async def _restore_deleted_version(
    session: AsyncSession,
    source_key: str,
    version: KnowledgeDocumentVersion,
    document: ExtractedDocument,
) -> None:
    knowledge_document = (
        await session.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.source_key == source_key
            )
        )
    ).scalar_one_or_none()
    if knowledge_document is None:
        return
    if knowledge_document.deleted_at is None:
        return
    _sync_document_metadata(knowledge_document, document)
    await _sync_knowledge_source(session, knowledge_document, document)
    await session.execute(
        update(KnowledgeDocumentVersion)
        .where(
            KnowledgeDocumentVersion.document_id == knowledge_document.id,
            KnowledgeDocumentVersion.id != version.id,
            KnowledgeDocumentVersion.status == "published",
        )
        .values(status="retired")
    )
    version.status = "published"
    version.published_at = datetime.now(timezone.utc)
    knowledge_document.current_version_id = version.id
    knowledge_document.deleted_at = None
    await session.commit()


def _sync_document_metadata(
    knowledge_document: KnowledgeDocument,
    document: ExtractedDocument,
) -> None:
    knowledge_document.title = document.title
    knowledge_document.kb_type = document.kb_type
    knowledge_document.source_uri = document.file_name
    knowledge_document.applicability = document.applicability
    knowledge_document.effective_date = _parse_date(document.effective_date)
    knowledge_document.source_kind = document.source_kind
    knowledge_document.original_filename = document.original_filename


async def _sync_knowledge_source(
    session: AsyncSession,
    knowledge_document: KnowledgeDocument,
    document: ExtractedDocument,
) -> None:
    source = (
        await session.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.source_uri == document.file_name
            )
        )
    ).scalar_one_or_none()
    if source is None:
        return
    source.name = document.title
    source.kb_type = document.kb_type
    source.checksum = document.checksum
    source.document_id = knowledge_document.id


async def _mark_failed(
    session: AsyncSession,
    version_id: int,
    job_id: int,
    reason: str,
) -> None:
    now = datetime.now(timezone.utc)
    version = await session.get(KnowledgeDocumentVersion, version_id)
    job = await session.get(KnowledgeIngestionJob, job_id)
    if version is not None:
        version.status = "failed"
        version.parent_count = 0
        version.chunk_count = 0
        version.failure_reason = reason
        version.published_at = None
    if job is not None:
        job.status = "failed"
        job.parent_count = 0
        job.chunk_count = 0
        job.failure_reason = reason
        job.finished_at = now
    await session.commit()


def _parse_date(value: str | None):
    if not value:
        return None
    from datetime import date

    return date.fromisoformat(value)


def _error_reason(exc: Exception) -> str:
    message = str(exc).strip()
    return message or exc.__class__.__name__


def _checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def _existing_source(
    session: AsyncSession, uri: str
) -> KnowledgeSource | None:
    result = await session.execute(
        select(KnowledgeSource).where(KnowledgeSource.source_uri == uri)
    )
    return result.scalar_one_or_none()


async def index_manifest(
    session: AsyncSession,
    provider: EmbeddingProvider,
    manifest_path: str | Path,
    chunk_size: int = 400,
    overlap: int = 80,
) -> IndexResult:
    """Index all sources declared in ``manifest_path``.

    Sources are skipped when their content checksum is unchanged, making the
    operation idempotent.
    """
    manifest_path = Path(manifest_path)
    base_dir = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result = IndexResult()

    for entry in manifest.get("sources", []):
        result.sources_seen += 1
        uri = entry["uri"]
        kb_type = entry.get("kb_type", "product")
        name = entry.get("name", uri)
        doc_path = base_dir / uri
        try:
            content = doc_path.read_text(encoding="utf-8")
        except OSError as exc:
            result.errors.append(f"{uri}: {exc}")
            continue

        digest = _checksum(content)
        existing = await _existing_source(session, uri)
        if existing is not None and existing.checksum == digest:
            result.sources_skipped += 1
            continue

        # Validate before persisting anything for this source.
        try:
            parents = split_parents(content)
            children_by_parent: list[tuple[object, list[str]]] = []
            all_texts: list[str] = []
            for parent_block in parents:
                child_texts = split_children(
                    parent_block.content, chunk_size=chunk_size, overlap=overlap
                )
                for text in child_texts:
                    assert_no_dynamic_fields(text)
                children_by_parent.append((parent_block, child_texts))
                all_texts.extend(child_texts)
        except ValueError as exc:
            result.errors.append(f"{uri}: {exc}")
            continue

        if existing is not None:
            await session.delete(existing)
            await session.flush()

        source = KnowledgeSource(
            name=name, source_uri=uri, kb_type=kb_type, checksum=digest
        )
        session.add(source)
        await session.flush()

        embeddings = await provider.embed_documents(all_texts)
        if len(embeddings) != len(all_texts):
            result.errors.append(f"{uri}: embedding count mismatch")
            await session.rollback()
            continue

        chunk_counter = 0
        for parent_block, child_texts in children_by_parent:
            parent = KnowledgeParent(
                source_id=source.id,
                parent_index=parent_block.index,
                content=parent_block.content,
                meta={"heading": parent_block.heading},
            )
            session.add(parent)
            await session.flush()
            for text in child_texts:
                vector = embeddings[chunk_counter]
                if len(vector) != EMBEDDING_DIMENSION:
                    result.errors.append(
                        f"{uri}: embedding dimension {len(vector)} != "
                        f"{EMBEDDING_DIMENSION}"
                    )
                    await session.rollback()
                    continue
                session.add(
                    KnowledgeChunk(
                        parent_id=parent.id,
                        chunk_index=chunk_counter,
                        content=text,
                        embedding=vector,
                    )
                )
                chunk_counter += 1

        result.sources_indexed += 1
        result.chunks_indexed += chunk_counter
        await session.commit()

    return result
