"""Integration tests for versioned PDF knowledge ingestion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

import app.rag.indexer as indexer_module
from app.db.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeIngestionJob,
    KnowledgeParent,
    KnowledgeSource,
)
from app.rag.indexer import index_extracted_documents
from app.rag.pdf_extraction import ExtractedDocument, ExtractedParent
from tests.fakes import FakeEmbeddings

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "data" / "knowledge" / "pdf_sources.json"
DEMO_REGISTRY_PATH = ROOT / "data" / "knowledge" / "demo_sources.json"


def _document(
    checksum: str = "a" * 64,
    *,
    source_id: str = "guide-a",
    title: str = "产品 A 用户指南",
    content: str = "第一章 概述\n这是产品的静态使用说明。\n\n第二章 使用\n这是安全使用步骤。",
) -> ExtractedDocument:
    return ExtractedDocument(
        source_id=source_id,
        file_name=f"{source_id}.pdf",
        title=title,
        kb_type="product",
        applicability=f"{title}适用机型",
        effective_date=None,
        checksum=checksum,
        page_count=10,
        extraction_strategy="jd_clauses",
        parents=(
            ExtractedParent(
                index=0,
                content=content,
                section_path="第一章 概述 > 第一节 产品说明",
                page_start=2,
                page_end=3,
            ),
        ),
    )


class WrongCountEmbeddings(FakeEmbeddings):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = await super().embed_documents(texts)
        return vectors[:-1]


class WrongDimensionEmbeddings(FakeEmbeddings):
    def __init__(self) -> None:
        super().__init__(dim=8)


async def _one(db_session, model):
    return (await db_session.execute(select(model))).scalar_one()


@pytest.mark.anyio
async def test_index_extracted_document_publishes_traceable_version(db_session):
    result = await index_extracted_documents(
        db_session,
        FakeEmbeddings(),
        [_document()],
        embedding_model="fake-1024",
        chunk_size=24,
        overlap=6,
    )

    assert result.documents_indexed == 1
    assert result.versions_published == 1
    assert result.documents_skipped == 0
    assert result.errors == []

    document = await _one(db_session, KnowledgeDocument)
    version = await _one(db_session, KnowledgeDocumentVersion)
    source = await _one(db_session, KnowledgeSource)
    job = await _one(db_session, KnowledgeIngestionJob)
    parents = (await db_session.execute(select(KnowledgeParent))).scalars().all()
    chunks = (await db_session.execute(select(KnowledgeChunk))).scalars().all()

    assert document.current_version_id == version.id
    assert document.source_key == "guide-a"
    assert version.status == "published"
    assert version.embedding_model == "fake-1024"
    assert version.parent_count == len(parents) == 1
    assert version.chunk_count == len(chunks) == result.chunks_indexed
    assert all(parent.document_version_id == version.id for parent in parents)
    assert all(parent.source_id == source.id for parent in parents)
    assert all(chunk.document_version_id == version.id for chunk in chunks)
    assert all(chunk.page_start == 2 and chunk.page_end == 3 for chunk in chunks)
    assert all(chunk.section_path for chunk in chunks)
    assert source.document_id == document.id
    assert job.status == "succeeded"
    assert job.parent_count == len(parents)
    assert job.chunk_count == len(chunks)
    assert job.failure_reason == ""


@pytest.mark.anyio
async def test_index_extracted_documents_skips_same_version_configuration(
    db_session,
):
    document = _document()
    first = await index_extracted_documents(
        db_session,
        FakeEmbeddings(),
        [document],
        embedding_model="fake-1024",
        chunk_size=32,
        overlap=8,
    )
    second = await index_extracted_documents(
        db_session,
        FakeEmbeddings(),
        [document],
        embedding_model="fake-1024",
        chunk_size=32,
        overlap=8,
    )

    assert first.documents_indexed == 1
    assert second.documents_indexed == 0
    assert second.documents_skipped == 1
    versions = (
        await db_session.execute(select(KnowledgeDocumentVersion))
    ).scalars().all()
    jobs = (await db_session.execute(select(KnowledgeIngestionJob))).scalars().all()
    assert len(versions) == 1
    assert len(jobs) == 1
    chunk_count = await db_session.scalar(select(func.count(KnowledgeChunk.id)))
    assert chunk_count == first.chunks_indexed


@pytest.mark.anyio
async def test_new_checksum_publishes_new_version_without_deleting_old_rows(
    db_session,
):
    first = await index_extracted_documents(
        db_session,
        FakeEmbeddings(),
        [_document()],
        embedding_model="fake-1024",
        chunk_size=32,
        overlap=8,
    )
    old_chunk_count = first.chunks_indexed

    second = await index_extracted_documents(
        db_session,
        FakeEmbeddings(),
        [_document("b" * 64, content="第一章 新版说明\n新版仅变更静态使用说明。")],
        embedding_model="fake-1024",
        chunk_size=32,
        overlap=8,
    )

    document = await _one(db_session, KnowledgeDocument)
    versions = (
        await db_session.execute(
            select(KnowledgeDocumentVersion).order_by(
                KnowledgeDocumentVersion.version_number
            )
        )
    ).scalars().all()
    assert second.versions_published == 1
    assert len(versions) == 2
    assert versions[0].status == "retired"
    assert versions[1].status == "published"
    assert document.current_version_id == versions[1].id
    assert versions[1].version_number == 2

    chunks = (await db_session.execute(select(KnowledgeChunk))).scalars().all()
    assert len(chunks) > old_chunk_count
    assert {chunk.document_version_id for chunk in chunks} == {
        versions[0].id,
        versions[1].id,
    }


@pytest.mark.anyio
async def test_embedding_count_mismatch_keeps_previous_published_version(
    db_session,
):
    await index_extracted_documents(
        db_session,
        FakeEmbeddings(),
        [_document()],
        embedding_model="fake-1024",
        chunk_size=32,
        overlap=8,
    )
    old = (
        await db_session.execute(
            select(KnowledgeDocumentVersion).where(
                KnowledgeDocumentVersion.status == "published"
            )
        )
    ).scalar_one()
    old_chunk_count = await db_session.scalar(select(func.count(KnowledgeChunk.id)))

    result = await index_extracted_documents(
        db_session,
        WrongCountEmbeddings(),
        [_document("b" * 64, content="第一章 新版本\n新的静态说明。")],
        embedding_model="fake-1024",
        chunk_size=32,
        overlap=8,
    )

    assert result.documents_indexed == 0
    assert result.errors
    document = await _one(db_session, KnowledgeDocument)
    versions = (
        await db_session.execute(
            select(KnowledgeDocumentVersion).order_by(
                KnowledgeDocumentVersion.version_number
            )
        )
    ).scalars().all()
    failed = versions[-1]
    assert document.current_version_id == old.id
    assert old.status == "published"
    assert failed.status == "failed"
    assert "count" in failed.failure_reason.lower()
    assert await db_session.scalar(select(func.count(KnowledgeChunk.id))) == old_chunk_count

    failed_job = (
        await db_session.execute(
            select(KnowledgeIngestionJob).where(
                KnowledgeIngestionJob.document_version_id == failed.id
            )
        )
    ).scalar_one()
    assert failed_job.status == "failed"
    assert failed_job.parent_count == 0
    assert failed_job.chunk_count == 0
    assert "count" in failed_job.failure_reason.lower()


@pytest.mark.anyio
async def test_non_1024_embedding_keeps_previous_version_and_records_failure(
    db_session,
):
    await index_extracted_documents(
        db_session,
        FakeEmbeddings(),
        [_document()],
        embedding_model="fake-1024",
        chunk_size=32,
        overlap=8,
    )
    old = (
        await db_session.execute(
            select(KnowledgeDocumentVersion).where(
                KnowledgeDocumentVersion.status == "published"
            )
        )
    ).scalar_one()

    result = await index_extracted_documents(
        db_session,
        WrongDimensionEmbeddings(),
        [_document("b" * 64)],
        embedding_model="fake-1024",
        chunk_size=32,
        overlap=8,
    )

    assert result.documents_indexed == 0
    assert result.errors
    document = await _one(db_session, KnowledgeDocument)
    failed = (
        await db_session.execute(
            select(KnowledgeDocumentVersion).where(
                KnowledgeDocumentVersion.status == "failed"
            )
        )
    ).scalar_one()
    assert document.current_version_id == old.id
    assert "dimension" in failed.failure_reason.lower()


@pytest.mark.anyio
async def test_failed_version_can_be_retried_without_creating_duplicate(
    db_session,
):
    document = _document()
    failed = await index_extracted_documents(
        db_session,
        WrongCountEmbeddings(),
        [document],
        embedding_model="fake-1024",
        chunk_size=32,
        overlap=8,
    )
    retried = await index_extracted_documents(
        db_session,
        FakeEmbeddings(),
        [document],
        embedding_model="fake-1024",
        chunk_size=32,
        overlap=8,
    )

    assert failed.documents_indexed == 0
    assert retried.documents_indexed == 1
    versions = (
        await db_session.execute(select(KnowledgeDocumentVersion))
    ).scalars().all()
    jobs = (await db_session.execute(select(KnowledgeIngestionJob))).scalars().all()
    assert len(versions) == 1
    assert versions[0].status == "published"
    assert len(jobs) == 2
    assert {job.status for job in jobs} == {"failed", "succeeded"}


@pytest.mark.anyio
async def test_registered_pdf_entrypoint_ingests_official_source(
    db_session,
    tmp_path,
):
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    registry["sources"] = [
        source
        for source in registry["sources"]
        if source["source_id"] == "jd-lightning-refund"
    ]
    registry_path = tmp_path / "pdf_sources.json"
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False),
        encoding="utf-8",
    )

    entrypoint = getattr(indexer_module, "index_registered_pdfs", None)
    assert callable(entrypoint), "official PDF registry entrypoint is missing"

    result = await entrypoint(
        db_session,
        FakeEmbeddings(),
        registry_path,
        project_root=ROOT,
        embedding_model="fake-1024",
        chunk_size=64,
        overlap=12,
    )

    assert result.documents_seen == 1
    assert result.documents_indexed == 1
    assert result.versions_published == 1
    assert result.errors == []

    document = await _one(db_session, KnowledgeDocument)
    version = await _one(db_session, KnowledgeDocumentVersion)
    source = await _one(db_session, KnowledgeSource)
    chunks = (await db_session.execute(select(KnowledgeChunk))).scalars().all()

    assert document.source_key == "jd-lightning-refund"
    assert document.current_version_id == version.id
    assert version.pdf_file == "京东开放平台闪电退款服务规则.pdf"
    assert version.extraction_strategy == "jd_clauses"
    assert version.embedding_model == "fake-1024"
    assert version.status == "published"
    assert source.document_id == document.id
    assert source.source_uri == version.pdf_file
    assert result.chunks_indexed == version.chunk_count == len(chunks)
    assert all(chunk.document_version_id == version.id for chunk in chunks)


@pytest.mark.anyio
async def test_registered_pdf_entrypoint_ingests_all_official_sources(db_session):
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    result = await indexer_module.index_registered_pdfs(
        db_session,
        FakeEmbeddings(),
        REGISTRY_PATH,
        project_root=ROOT,
        embedding_model="fake-1024",
        chunk_size=64,
        overlap=12,
    )

    assert result.documents_seen == len(registry["sources"]) == 10
    assert result.documents_indexed == 10
    assert result.versions_published == 10
    assert result.documents_skipped == 0
    assert result.errors == []

    documents = (await db_session.execute(select(KnowledgeDocument))).scalars().all()
    versions = (
        await db_session.execute(select(KnowledgeDocumentVersion))
    ).scalars().all()
    jobs = (
        await db_session.execute(select(KnowledgeIngestionJob))
    ).scalars().all()
    chunk_count = await db_session.scalar(select(func.count(KnowledgeChunk.id)))

    assert len(documents) == 10
    assert len(versions) == 10
    assert len(jobs) == 10
    assert chunk_count == result.chunks_indexed
    assert {document.source_key for document in documents} == {
        source["source_id"] for source in registry["sources"]
    }
    assert all(document.current_version_id is not None for document in documents)
    assert all(version.status == "published" for version in versions)
    assert all(job.status == "succeeded" for job in jobs)


@pytest.mark.anyio
async def test_registered_pdf_entrypoint_reports_bad_source_and_continues(
    db_session,
    tmp_path,
):
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    valid = next(
        source
        for source in registry["sources"]
        if source["source_id"] == "jd-lightning-refund"
    )
    tampered = {
        **valid,
        "source_id": "jd-lightning-refund-tampered",
        "checksum": "0" * 64,
    }
    registry["sources"] = [tampered, valid]
    registry_path = tmp_path / "pdf_sources.json"
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False),
        encoding="utf-8",
    )

    result = await indexer_module.index_registered_pdfs(
        db_session,
        FakeEmbeddings(),
        registry_path,
        project_root=ROOT,
        embedding_model="fake-1024",
        chunk_size=64,
        overlap=12,
    )

    assert result.documents_seen == 2
    assert result.documents_indexed == 1
    assert len(result.errors) == 1
    assert "jd-lightning-refund-tampered" in result.errors[0]
    assert "checksum" in result.errors[0].lower()
    assert await db_session.scalar(select(func.count(KnowledgeDocument.id))) == 1
    document = await _one(db_session, KnowledgeDocument)
    assert document.source_key == "jd-lightning-refund"


@pytest.mark.anyio
async def test_registered_markdown_entrypoint_indexes_demo_bundle(db_session):
    result = await indexer_module.index_registered_markdowns(
        db_session,
        FakeEmbeddings(),
        DEMO_REGISTRY_PATH,
        project_root=ROOT,
        embedding_model="fake-1024",
        chunk_size=64,
        overlap=12,
    )

    assert result.documents_seen == 8
    assert result.documents_indexed == 8
    assert result.versions_published == 8
    assert result.errors == []

    documents = (
        await db_session.execute(select(KnowledgeDocument))
    ).scalars().all()
    versions = (
        await db_session.execute(select(KnowledgeDocumentVersion))
    ).scalars().all()
    chunks = (await db_session.execute(select(KnowledgeChunk))).scalars().all()

    assert len(documents) == 8
    assert len(versions) == 8
    assert all(document.current_version_id is not None for document in documents)
    assert all(document.source_kind == "text" for document in documents)
    assert all(version.status == "published" for version in versions)
    assert all(version.extraction_strategy == "markdown" for version in versions)
    assert len(chunks) == result.chunks_indexed
