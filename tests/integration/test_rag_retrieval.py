"""Integration tests for the full retrieval pipeline against PGVector."""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from app.db.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeParent,
    KnowledgeSource,
)
from app.rag.retriever import Providers, retrieve_parent_context
from tests.fakes import FakeReranker, QueryVectorEmbeddings

DIM = 1024


def vec(index: int, value: float = 1.0) -> list[float]:
    v = [0.0] * DIM
    v[index] = value
    return v


async def _create_document(
    db_session,
    *,
    source_key: str,
    title: str,
    kb_type: str,
    source_uri: str,
) -> tuple[KnowledgeDocument, KnowledgeSource]:
    document = KnowledgeDocument(
        source_key=source_key,
        title=title,
        kb_type=kb_type,
        source_uri=source_uri,
        applicability="测试适用范围",
        effective_date=date(2026, 11, 18),
    )
    db_session.add(document)
    await db_session.flush()
    source = KnowledgeSource(
        name=title,
        source_uri=source_uri,
        kb_type=kb_type,
        checksum="0" * 64,
        document_id=document.id,
    )
    db_session.add(source)
    await db_session.flush()
    return document, source


async def _add_version(
    db_session,
    document: KnowledgeDocument,
    source: KnowledgeSource,
    *,
    version_number: int,
    status: str,
    checksum: str,
    pdf_file: str,
    page_start: int,
    page_end: int,
    section_path: str,
    parent_content: str,
    child_contents: list[str],
    embedding: list[float],
    make_current: bool = False,
) -> tuple[KnowledgeDocumentVersion, KnowledgeParent]:
    version = KnowledgeDocumentVersion(
        document_id=document.id,
        version_number=version_number,
        checksum=checksum,
        chunk_size=400,
        overlap=80,
        status=status,
        pdf_file=pdf_file,
        page_count=10,
        extraction_strategy="jd_clauses",
        embedding_model="test-1024",
        parent_count=1,
        chunk_count=len(child_contents),
    )
    db_session.add(version)
    await db_session.flush()

    parent = KnowledgeParent(
        source_id=source.id,
        parent_index=version_number - 1,
        content=parent_content,
        meta={},
        document_version_id=version.id,
        page_start=page_start,
        page_end=page_end,
        section_path=section_path,
    )
    db_session.add(parent)
    await db_session.flush()
    for chunk_index, content in enumerate(child_contents):
        db_session.add(
            KnowledgeChunk(
                parent_id=parent.id,
                chunk_index=chunk_index,
                content=content,
                embedding=embedding,
                document_version_id=version.id,
                page_start=page_start,
                page_end=page_end,
                section_path=section_path,
            )
        )

    if make_current:
        document.current_version_id = version.id
    await db_session.flush()
    return version, parent


@pytest.mark.anyio
async def test_retrieve_only_returns_current_published_version_with_traceability(
    db_session,
):
    document, source = await _create_document(
        db_session,
        source_key="guide-a",
        title="产品 A 用户指南",
        kb_type="product",
        source_uri="guide-a.pdf",
    )
    current, current_parent = await _add_version(
        db_session,
        document,
        source,
        version_number=2,
        status="published",
        checksum="a" * 64,
        pdf_file="guide-a.pdf",
        page_start=4,
        page_end=6,
        section_path="第一章 使用 > 第一节 开机",
        parent_content="当前发布版本的完整父块内容。",
        child_contents=["当前版本子块"],
        embedding=vec(0),
        make_current=True,
    )
    for number, status in ((1, "retired"), (3, "draft"), (4, "failed")):
        await _add_version(
            db_session,
            document,
            source,
            version_number=number,
            status=status,
        checksum=f"{number:064x}",
            pdf_file=f"guide-a-v{number}.pdf",
            page_start=1,
            page_end=2,
            section_path=f"不可返回的{status}章节",
            parent_content=f"不可返回的{status}父块。",
            child_contents=[f"不可返回的{status}子块"],
            embedding=vec(0),
        )

    legacy_source = KnowledgeSource(
        name="旧 Markdown",
        source_uri="legacy.md",
        kb_type="product",
        checksum="f" * 64,
    )
    db_session.add(legacy_source)
    await db_session.flush()
    legacy_parent = KnowledgeParent(
        source_id=legacy_source.id,
        parent_index=0,
        content="没有正式 PDF 版本的旧父块。",
        meta={},
    )
    db_session.add(legacy_parent)
    await db_session.flush()
    db_session.add(
        KnowledgeChunk(
            parent_id=legacy_parent.id,
            chunk_index=0,
            content="没有正式 PDF 版本的旧子块。",
            embedding=vec(0),
        )
    )
    await db_session.commit()

    providers = Providers(
        embeddings=QueryVectorEmbeddings(vec(0)),
        rerank=FakeReranker(),
    )
    result = await retrieve_parent_context(
        db_session, "怎么开机", "product", providers, recall_k=12, rerank_k=10
    )

    assert result.parent_ids == [current_parent.id]
    assert len(result.sources) == 1
    citation = result.sources[0]
    assert citation.document_version_id == current.id
    assert citation.file_name == "guide-a.pdf"
    assert citation.checksum == "a" * 64
    assert citation.page_start == 4
    assert citation.page_end == 6
    assert citation.section_path == "第一章 使用 > 第一节 开机"
    assert "当前发布版本的完整父块内容" in result.context
    assert all(
        forbidden not in result.context
        for forbidden in ("retired父块", "draft父块", "failed父块", "旧父块")
    )


@pytest.mark.anyio
async def test_retrieve_reranks_expands_and_dedupes_current_versions(db_session):
    document_a, source_a = await _create_document(
        db_session,
        source_key="guide-a",
        title="产品A",
        kb_type="product",
        source_uri="p/a.pdf",
    )
    await _add_version(
        db_session,
        document_a,
        source_a,
        version_number=1,
        status="published",
        checksum="a" * 64,
        pdf_file="a.pdf",
        page_start=1,
        page_end=2,
        section_path="产品A > 使用",
        parent_content="父块A完整段落内容很长很长。",
        child_contents=["子块A1"],
        embedding=vec(0, 1.0),
        make_current=True,
    )
    document_b, source_b = await _create_document(
        db_session,
        source_key="guide-b",
        title="产品B",
        kb_type="product",
        source_uri="p/b.pdf",
    )
    _, parent_b = await _add_version(
        db_session,
        document_b,
        source_b,
        version_number=1,
        status="published",
        checksum="b" * 64,
        pdf_file="b.pdf",
        page_start=3,
        page_end=4,
        section_path="产品B > 使用",
        parent_content="父块B完整段落内容也很长。",
        child_contents=["子块B1", "子块B2"],
        embedding=vec(0, 0.9),
        make_current=True,
    )
    await db_session.commit()

    providers = Providers(
        embeddings=QueryVectorEmbeddings(vec(0, 1.0)),
        rerank=FakeReranker(order=[2, 1, 0]),
    )
    result = await retrieve_parent_context(
        db_session, "查询", "product", providers, recall_k=12, rerank_k=3
    )

    assert result.parent_ids
    assert result.parent_ids[0] == parent_b.id
    assert len(result.parent_ids) == len(set(result.parent_ids))
    assert len(result.sources) == len(result.parent_ids)
    assert "父块" in result.context
    assert "子块" not in result.context


@pytest.mark.anyio
async def test_retrieve_respects_kb_type_filter(db_session):
    product_document, product_source = await _create_document(
        db_session,
        source_key="product-a",
        title="产品A",
        kb_type="product",
        source_uri="p/a.pdf",
    )
    await _add_version(
        db_session,
        product_document,
        product_source,
        version_number=1,
        status="published",
        checksum="a" * 64,
        pdf_file="a.pdf",
        page_start=1,
        page_end=1,
        section_path="产品章节",
        parent_content="产品父块内容",
        child_contents=["产品子块"],
        embedding=vec(0),
        make_current=True,
    )
    policy_document, policy_source = await _create_document(
        db_session,
        source_key="policy-p",
        title="政策P",
        kb_type="policy",
        source_uri="p/p.pdf",
    )
    await _add_version(
        db_session,
        policy_document,
        policy_source,
        version_number=1,
        status="published",
        checksum="b" * 64,
        pdf_file="p.pdf",
        page_start=1,
        page_end=1,
        section_path="政策章节",
        parent_content="政策父块内容",
        child_contents=["政策子块"],
        embedding=vec(0),
        make_current=True,
    )
    await db_session.commit()

    providers = Providers(
        embeddings=QueryVectorEmbeddings(vec(0)),
        rerank=FakeReranker(),
    )
    product_result = await retrieve_parent_context(
        db_session, "查询", "product", providers, recall_k=12, rerank_k=5
    )

    assert product_result.sources
    assert all(source.kb_type == "product" for source in product_result.sources)
    assert "产品父块内容" in product_result.context
    assert "政策父块内容" not in product_result.context


@pytest.mark.anyio
async def test_retrieve_excludes_soft_deleted_documents(db_session):
    document, source = await _create_document(
        db_session,
        source_key="deleted-guide",
        title="已删除产品指南",
        kb_type="product",
        source_uri="deleted.pdf",
    )
    await _add_version(
        db_session,
        document,
        source,
        version_number=1,
        status="published",
        checksum="d" * 64,
        pdf_file="deleted.pdf",
        page_start=1,
        page_end=1,
        section_path="已删除章节",
        parent_content="这段内容不能再次被检索。",
        child_contents=["已删除子块"],
        embedding=vec(0),
        make_current=True,
    )
    document.deleted_at = sa.func.now()
    await db_session.commit()

    result = await retrieve_parent_context(
        db_session,
        "查询",
        "product",
        Providers(
            embeddings=QueryVectorEmbeddings(vec(0)),
            rerank=FakeReranker(),
        ),
        recall_k=12,
        rerank_k=5,
    )
    assert result.is_empty is True


@pytest.mark.anyio
async def test_retrieve_empty_result_is_explicit(db_session):
    providers = Providers(
        embeddings=QueryVectorEmbeddings(vec(0)),
        rerank=FakeReranker(),
    )
    result = await retrieve_parent_context(
        db_session, "查询", "policy", providers, recall_k=12, rerank_k=5
    )
    assert result.is_empty is True
    assert result.sources == []
