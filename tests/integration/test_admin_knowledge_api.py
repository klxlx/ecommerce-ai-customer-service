"""Admin knowledge-management API integration tests."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import date
from io import BytesIO
from decimal import Decimal

import pytest
import pymupdf
from docx import Document
from PIL import Image
from sqlalchemy import select

from app.api.deps import get_container
from app.db.models import (
    AfterSale,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeParent,
    KnowledgeSource,
    LogisticsEvent,
    Order,
    Product,
)
from app.graph.container import Container
from app.rag.retriever import Providers, retrieve_parent_context
from app.services.knowledge_import import get_ocr_provider
from tests.fakes import (
    FakeChat,
    FakeEmbeddings,
    FakeReranker,
    QueryVectorEmbeddings,
)
from tests.helpers import (
    create_after_sale,
    create_order,
    create_product,
    create_user,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client, username: str) -> str:
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "password123"},
    )
    return response.json()["token"]


class StubOCR:
    def extract_text(self, image_bytes: bytes) -> str:
        assert image_bytes
        return "扫描件中识别到的固定安装步骤"


def _use_fake_providers(app) -> None:
    _use_providers(app, FakeEmbeddings())


def _use_providers(app, embeddings) -> None:
    container = Container(
        chat=FakeChat(),
        providers=Providers(
            embeddings=embeddings,
            rerank=FakeReranker(),
        ),
        settings=app.state.settings,
    )
    app.dependency_overrides[get_container] = lambda: container


def _docx_bytes() -> bytes:
    document = Document()
    document.add_heading("安装说明", level=1)
    document.add_paragraph("请先连接官方电源。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "步骤"
    table.cell(0, 1).text = "操作"
    table.cell(1, 0).text = "1"
    table.cell(1, 1).text = "开机自检"
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _text_pdf_bytes() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Warranty requires the original invoice.")
    data = document.tobytes()
    document.close()
    return data


def _image_pdf_bytes() -> bytes:
    image = Image.new("RGB", (240, 90), "white")
    image_buffer = BytesIO()
    image.save(image_buffer, format="PNG")
    document = pymupdf.open()
    page = document.new_page()
    page.insert_image(page.rect, stream=image_buffer.getvalue())
    data = document.tobytes()
    document.close()
    return data


def _image_bytes() -> bytes:
    image = Image.new("RGB", (180, 80), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.anyio
@pytest.mark.parametrize("kb_type", ["product", "policy"])
async def test_admin_can_add_and_ingest_product_or_policy_knowledge(
    app,
    db_session,
    client,
    kb_type,
):
    await create_user(db_session, "admin1", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "admin1")

    response = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "静态售后说明",
            "kb_type": kb_type,
            "applicability": "适用于全部官方渠道",
            "content": "## 适用范围\n仅适用于官方渠道购买的商品。\n\n"
            "## 处理原则\n资料只说明固定规则，不包含订单状态。",
        },
        headers=_auth(token),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ingestion"]["status"] == "succeeded"
    assert body["ingestion"]["success_count"] == 1
    assert body["ingestion"]["versions_published"] == 1
    assert body["ingestion"]["chunks_indexed"] > 0
    assert body["ingestion"]["failure_reason"] == ""
    assert body["item"]["kb_type"] == kb_type
    assert body["item"]["origin"] == "manual"
    assert body["item"]["can_edit"] is True
    assert body["item"]["current_version_number"] == 1
    assert body["item"]["status"] == "published"
    assert body["item"]["chunk_count"] == body["ingestion"]["chunks_indexed"]
    assert body["item"]["latest_job"]["status"] == "succeeded"
    assert body["item"]["latest_job"]["chunk_count"] == body["item"]["chunk_count"]

    document = (
        await db_session.execute(select(KnowledgeDocument))
    ).scalar_one()
    version = (
        await db_session.execute(select(KnowledgeDocumentVersion))
    ).scalar_one()
    chunk_count = len(
        (await db_session.execute(select(KnowledgeChunk))).scalars().all()
    )
    assert document.current_version_id == version.id
    assert version.status == "published"
    assert version.extraction_strategy == "manual"
    assert version.chunk_count == chunk_count


@pytest.mark.anyio
async def test_admin_update_creates_new_version_and_keeps_old_version(
    app,
    db_session,
    client,
):
    await create_user(db_session, "admin1", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "admin1")
    first = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "安装说明",
            "kb_type": "product",
            "content": "## 安装\n使用官方提供的固定安装步骤。",
        },
        headers=_auth(token),
    )
    source_key = first.json()["item"]["source_key"]

    second = await client.post(
        "/api/admin/knowledge",
        json={
            "source_key": source_key,
            "title": "安装说明（新版）",
            "kb_type": "product",
            "content": "## 安装\n新版继续使用官方固定安装步骤。",
        },
        headers=_auth(token),
    )

    assert second.status_code == 200
    body = second.json()
    assert body["item"]["source_key"] == source_key
    assert body["item"]["current_version_number"] == 2
    assert body["ingestion"]["versions_published"] == 1

    versions = (
        await db_session.execute(
            select(KnowledgeDocumentVersion).order_by(
                KnowledgeDocumentVersion.version_number
            )
        )
    ).scalars().all()
    assert [version.status for version in versions] == ["retired", "published"]
    document = (
        await db_session.execute(select(KnowledgeDocument))
    ).scalar_one()
    assert document.current_version_id == versions[1].id


@pytest.mark.anyio
async def test_manual_upsert_cannot_replace_registered_pdf(
    app,
    db_session,
    client,
):
    await create_user(db_session, "admin1", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "admin1")
    document = KnowledgeDocument(
        source_key="official-product-guide",
        title="官方产品指南",
        kb_type="product",
        source_uri="official-product-guide.pdf",
        applicability="官方渠道",
    )
    db_session.add(document)
    await db_session.flush()
    version = KnowledgeDocumentVersion(
        document_id=document.id,
        version_number=1,
        checksum="a" * 64,
        chunk_size=400,
        overlap=80,
        status="published",
        pdf_file="official-product-guide.pdf",
        page_count=10,
        extraction_strategy="jd_clauses",
        embedding_model="test-1024",
        parent_count=1,
        chunk_count=1,
    )
    db_session.add(version)
    await db_session.flush()
    document.current_version_id = version.id
    await db_session.commit()

    response = await client.post(
        "/api/admin/knowledge",
        json={
            "source_key": document.source_key,
            "title": "试图覆盖的指南",
            "kb_type": "product",
            "content": "## 覆盖内容\n不允许通过手工接口修改正式 PDF。",
        },
        headers=_auth(token),
    )

    assert response.status_code == 409
    assert "正式 PDF" in response.json()["detail"]
    versions = (
        await db_session.execute(select(KnowledgeDocumentVersion))
    ).scalars().all()
    stored = await db_session.get(KnowledgeDocument, document.id)
    assert len(versions) == 1
    assert versions[0].id == version.id
    assert versions[0].status == "published"
    assert stored is not None
    assert stored.current_version_id == version.id
    assert stored.title == "官方产品指南"


@pytest.mark.anyio
async def test_unchanged_knowledge_is_reported_without_duplicate_version(
    app,
    db_session,
    client,
):
    await create_user(db_session, "admin1", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "admin1")
    payload = {
        "title": "固定保修说明",
        "kb_type": "policy",
        "applicability": "全部商品",
        "content": "## 保修\n保修规则以商品页面公示为准。",
    }
    first = await client.post(
        "/api/admin/knowledge",
        json=payload,
        headers=_auth(token),
    )
    assert first.json()["ingestion"]["status"] == "succeeded"

    second = await client.post(
        "/api/admin/knowledge",
        json=payload,
        headers=_auth(token),
    )

    assert second.status_code == 200
    body = second.json()
    assert body["ingestion"]["status"] == "unchanged"
    assert body["ingestion"]["success_count"] == 0
    assert body["ingestion"]["documents_skipped"] == 1
    versions = (
        await db_session.execute(select(KnowledgeDocumentVersion))
    ).scalars().all()
    assert len(versions) == 1


@pytest.mark.anyio
async def test_manual_knowledge_title_change_reuses_same_document(
    app,
    db_session,
    client,
):
    await create_user(db_session, "title-change-admin", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "title-change-admin")
    content = (
        "## 保修规则\n"
        "适用范围：全部官方渠道\n"
        "生效日期：2026-11-01\n"
        "保修期限以商品页面公示为准。"
    )
    first = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "保修说明",
            "kb_type": "policy",
            "content": content,
        },
        headers=_auth(token),
    )
    first_item = first.json()["item"]
    original_parent_ids = (
        await db_session.execute(select(KnowledgeParent.id))
    ).scalars().all()

    second = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "保修说明（客服版）",
            "kb_type": "policy",
            "content": content,
        },
        headers=_auth(token),
    )

    assert second.status_code == 200
    body = second.json()
    assert body["ingestion"]["status"] == "unchanged"
    assert body["item"]["id"] == first_item["id"]
    assert body["item"]["title"] == "保修说明（客服版）"
    documents = (
        await db_session.execute(select(KnowledgeDocument))
    ).scalars().all()
    versions = (
        await db_session.execute(select(KnowledgeDocumentVersion))
    ).scalars().all()
    parent_ids = (
        await db_session.execute(select(KnowledgeParent.id))
    ).scalars().all()
    assert len(documents) == 1
    assert len(versions) == 1
    assert parent_ids == original_parent_ids


@pytest.mark.anyio
async def test_manual_knowledge_title_change_reuses_legacy_source_key(
    app,
    db_session,
    client,
):
    await create_user(db_session, "legacy-title-admin", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "legacy-title-admin")
    content = (
        "## 保修规则\n"
        "适用范围：全部官方渠道\n"
        "生效日期：2026-11-01\n"
        "保修期限以商品页面公示为准。"
    )
    legacy_source_key = (
        "manual-3e978340f9508cd72f4f72fef3915fe057a45a2d74721e9f8cbd1abff6a538d3"
    )
    legacy_checksum = legacy_source_key.removeprefix("manual-")
    document = KnowledgeDocument(
        source_key=legacy_source_key,
        title="旧版保修说明",
        kb_type="policy",
        source_uri=f"manual://{legacy_source_key}",
        applicability="全部官方渠道",
        effective_date=date(2026, 11, 1),
        source_kind="manual",
        original_filename="",
    )
    db_session.add(document)
    await db_session.flush()
    version = KnowledgeDocumentVersion(
        document_id=document.id,
        version_number=1,
        checksum=legacy_checksum,
        chunk_size=400,
        overlap=80,
        status="published",
        pdf_file=f"manual://{legacy_source_key}",
        page_count=0,
        extraction_strategy="manual",
        embedding_model=app.state.settings.embedding_model,
        parent_count=1,
        chunk_count=1,
    )
    db_session.add(version)
    await db_session.flush()
    document.current_version_id = version.id
    source = KnowledgeSource(
        name=document.title,
        source_uri=document.source_uri,
        kb_type=document.kb_type,
        checksum=legacy_checksum,
        document_id=document.id,
    )
    db_session.add(source)
    await db_session.flush()
    parent = KnowledgeParent(
        source_id=source.id,
        parent_index=0,
        content=content.removeprefix("## 保修规则\n"),
        meta={"title": document.title},
        document_version_id=version.id,
        section_path="保修规则",
    )
    db_session.add(parent)
    await db_session.commit()
    original_parent_ids = [parent.id]

    response = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "保修说明（新版标题）",
            "kb_type": "policy",
            "content": content,
        },
        headers=_auth(token),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ingestion"]["status"] == "unchanged"
    assert body["item"]["id"] == document.id
    assert body["item"]["source_key"] == legacy_source_key
    assert body["item"]["title"] == "保修说明（新版标题）"
    documents = (
        await db_session.execute(select(KnowledgeDocument))
    ).scalars().all()
    versions = (
        await db_session.execute(select(KnowledgeDocumentVersion))
    ).scalars().all()
    parent_ids = (
        await db_session.execute(select(KnowledgeParent.id))
    ).scalars().all()
    assert len(documents) == 1
    assert len(versions) == 1
    assert parent_ids == original_parent_ids


@pytest.mark.anyio
async def test_concurrent_manual_submissions_are_deduplicated(
    app,
    db_session,
    client,
):
    await create_user(db_session, "admin1", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "admin1")
    payload = {
        "title": "并发保修说明",
        "kb_type": "policy",
        "applicability": "全部商品",
        "content": "## 保修\n并发提交也只能生成一份知识。",
    }

    responses = await asyncio.gather(
        client.post(
            "/api/admin/knowledge",
            json=payload,
            headers=_auth(token),
        ),
        client.post(
            "/api/admin/knowledge",
            json=payload,
            headers=_auth(token),
        ),
    )

    assert [response.status_code for response in responses] == [200, 200]
    statuses = sorted(
        response.json()["ingestion"]["status"] for response in responses
    )
    assert statuses == ["succeeded", "unchanged"]
    documents = (
        await db_session.execute(select(KnowledgeDocument))
    ).scalars().all()
    versions = (
        await db_session.execute(select(KnowledgeDocumentVersion))
    ).scalars().all()
    assert len(documents) == 1
    assert len(versions) == 1


@pytest.mark.anyio
async def test_failed_update_returns_safe_reason_and_keeps_published_version(
    app,
    db_session,
    client,
):
    await create_user(db_session, "admin1", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "admin1")
    first = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "价格说明",
            "kb_type": "product",
            "content": "## 价格展示\n价格以商品页面实时数据为准。",
        },
        headers=_auth(token),
    )
    source_key = first.json()["item"]["source_key"]

    failed = await client.post(
        "/api/admin/knowledge",
        json={
            "source_key": source_key,
            "title": "价格说明",
            "kb_type": "product",
            "content": "价格：99 元，库存：10 件。",
        },
        headers=_auth(token),
    )

    assert failed.status_code == 200
    body = failed.json()
    assert body["ingestion"]["status"] == "failed"
    assert body["ingestion"]["success_count"] == 0
    assert "dynamic" in body["ingestion"]["failure_reason"].lower()
    assert "traceback" not in body["ingestion"]["failure_reason"].lower()
    document = (
        await db_session.execute(select(KnowledgeDocument))
    ).scalar_one()
    versions = (
        await db_session.execute(
            select(KnowledgeDocumentVersion).order_by(
                KnowledgeDocumentVersion.version_number
            )
        )
    ).scalars().all()
    assert len(versions) == 1
    assert document.current_version_id == versions[0].id
    assert versions[0].status == "published"


@pytest.mark.anyio
async def test_admin_lists_knowledge_status_and_can_open_editable_content(
    app,
    db_session,
    client,
):
    await create_user(db_session, "admin1", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "admin1")
    content = "## 发货时间\n固定工作日 48 小时内安排发货。"
    created = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "发货规则",
            "kb_type": "policy",
            "applicability": "现货商品",
            "content": content,
        },
        headers=_auth(token),
    )

    listing = await client.get(
        "/api/admin/knowledge",
        headers=_auth(token),
    )

    assert listing.status_code == 200
    items = listing.json()
    assert len(items) == 1
    assert items[0]["source_key"] == created.json()["item"]["source_key"]
    assert items[0]["status"] == "published"
    assert items[0]["current_version_number"] == 1
    assert items[0]["latest_job"]["status"] == "succeeded"

    detail = await client.get(
        f"/api/admin/knowledge/{items[0]['id']}",
        headers=_auth(token),
    )
    assert detail.status_code == 200
    assert detail.json()["content"] == content
    assert detail.json()["can_edit"] is True


@pytest.mark.anyio
async def test_created_manual_knowledge_is_retrievable_with_traceable_source(
    app,
    db_session,
    client,
):
    await create_user(db_session, "retrieval-admin", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "retrieval-admin")
    title = "桌面端延保登记说明"
    content = (
        "## 延保范围\n"
        "仅适用于通过官方桌面端完成登记且仍在保修期内的指定商品。"
    )

    response = await client.post(
        "/api/admin/knowledge",
        json={
            "title": title,
            "kb_type": "policy",
            "applicability": "官方桌面端",
            "content": content,
        },
        headers=_auth(token),
    )

    assert response.status_code == 200
    item = response.json()["item"]
    version = (
        await db_session.execute(select(KnowledgeDocumentVersion))
    ).scalar_one()
    chunk = (await db_session.execute(select(KnowledgeChunk))).scalar_one()
    result = await retrieve_parent_context(
        db_session,
        "如何登记延保",
        "policy",
        Providers(
            embeddings=QueryVectorEmbeddings(list(chunk.embedding)),
            rerank=FakeReranker(),
        ),
    )

    assert result.sources
    citation = result.sources[0]
    assert citation.title == title
    assert citation.source_uri == f"manual://{item['source_key']}"
    assert citation.file_name == f"manual://{item['source_key']}"
    assert citation.checksum == version.checksum
    assert citation.document_version_id == version.id
    assert citation.page_start is None
    assert citation.page_end is None
    assert citation.section_path == "延保范围"
    assert "仍在保修期内的指定商品" in result.context


@pytest.mark.anyio
async def test_updated_manual_knowledge_keeps_old_rows_and_retrieves_new_version(
    app,
    db_session,
    client,
):
    await create_user(db_session, "version-admin", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "version-admin")
    first = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "桌面端安装说明",
            "kb_type": "product",
            "content": "## 安装步骤\n旧版说明要求先连接官方电源。",
        },
        headers=_auth(token),
    )
    source_key = first.json()["item"]["source_key"]

    second = await client.post(
        "/api/admin/knowledge",
        json={
            "source_key": source_key,
            "title": "桌面端安装说明",
            "kb_type": "product",
            "content": "## 安装步骤\n新版说明要求先完成设备自检。",
        },
        headers=_auth(token),
    )

    assert second.status_code == 200
    versions = (
        await db_session.execute(
            select(KnowledgeDocumentVersion).order_by(
                KnowledgeDocumentVersion.version_number
            )
        )
    ).scalars().all()
    assert [version.status for version in versions] == ["retired", "published"]
    chunks = (await db_session.execute(select(KnowledgeChunk))).scalars().all()
    old_chunks = [
        chunk for chunk in chunks if chunk.document_version_id == versions[0].id
    ]
    current_chunks = [
        chunk for chunk in chunks if chunk.document_version_id == versions[1].id
    ]
    assert old_chunks
    assert current_chunks
    assert any("旧版说明" in chunk.content for chunk in old_chunks)
    assert any("新版说明" in chunk.content for chunk in current_chunks)

    result = await retrieve_parent_context(
        db_session,
        "安装前要做什么",
        "product",
        Providers(
            embeddings=QueryVectorEmbeddings(list(current_chunks[0].embedding)),
            rerank=FakeReranker(),
        ),
    )

    assert result.sources
    assert result.sources[0].document_version_id == versions[1].id
    assert "新版说明" in result.context
    assert "旧版说明" not in result.context


@pytest.mark.anyio
async def test_deleted_knowledge_with_same_content_restores_existing_version(
    app,
    db_session,
    client,
):
    await create_user(db_session, "delete-knowledge-admin", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "delete-knowledge-admin")
    created = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "待删除资料",
            "kb_type": "policy",
            "content": "## 删除测试\n这段资料删除后不可再被检索。",
        },
        headers=_auth(token),
    )
    item = created.json()["item"]

    deleted = await client.delete(
        f"/api/admin/knowledge/{item['id']}",
        headers=_auth(token),
    )
    assert deleted.status_code == 204
    assert (await client.get("/api/admin/knowledge", headers=_auth(token))).json() == []
    detail = await client.get(
        f"/api/admin/knowledge/{item['id']}",
        headers=_auth(token),
    )
    assert detail.status_code == 404

    restored = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "待删除资料",
            "kb_type": "policy",
            "content": "## 删除测试\n这段资料删除后不可再被检索。",
        },
        headers=_auth(token),
    )
    assert restored.status_code == 200
    assert restored.json()["ingestion"]["status"] == "unchanged"
    assert restored.json()["item"]["id"] == item["id"]
    assert restored.json()["item"]["current_version_number"] == 1
    assert (await client.get("/api/admin/knowledge", headers=_auth(token))).json()

    document = await db_session.get(KnowledgeDocument, item["id"])
    versions = (
        await db_session.execute(
            select(KnowledgeDocumentVersion).where(
                KnowledgeDocumentVersion.document_id == item["id"]
            )
        )
    ).scalars().all()
    parents = (
        await db_session.execute(
            select(KnowledgeParent).where(
                KnowledgeParent.document_version_id == versions[0].id
            )
        )
    ).scalars().all()
    chunks = (
        await db_session.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.document_version_id == versions[0].id
            )
        )
    ).scalars().all()
    assert document is not None
    assert document.deleted_at is None
    assert len(versions) == 1
    assert versions[0].status == "published"
    assert parents
    assert chunks


@pytest.mark.anyio
async def test_deleted_knowledge_restores_retired_version(
    app,
    db_session,
    client,
):
    await create_user(db_session, "restore-retired-admin", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "restore-retired-admin")
    first_content = "## 规则\n第一版政策要求保留原始凭证。"
    first = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "凭证政策",
            "kb_type": "policy",
            "content": first_content,
        },
        headers=_auth(token),
    )
    source_key = first.json()["source_key"]

    second = await client.post(
        "/api/admin/knowledge",
        json={
            "source_key": source_key,
            "title": "凭证政策",
            "kb_type": "policy",
            "content": "## 规则\n第二版政策要求上传电子凭证。",
        },
        headers=_auth(token),
    )
    assert second.status_code == 200

    item = second.json()["item"]
    deleted = await client.delete(
        f"/api/admin/knowledge/{item['id']}",
        headers=_auth(token),
    )
    assert deleted.status_code == 204

    restored = await client.post(
        "/api/admin/knowledge",
        json={
            "source_key": source_key,
            "title": "凭证政策",
            "kb_type": "policy",
            "content": first_content,
        },
        headers=_auth(token),
    )

    assert restored.status_code == 200
    assert restored.json()["ingestion"]["status"] == "unchanged"
    assert restored.json()["item"]["id"] == item["id"]
    assert restored.json()["item"]["current_version_number"] == 1
    assert restored.json()["item"]["status"] == "published"

    document = await db_session.get(KnowledgeDocument, item["id"])
    versions = (
        await db_session.execute(
            select(KnowledgeDocumentVersion)
            .where(KnowledgeDocumentVersion.document_id == item["id"])
            .order_by(KnowledgeDocumentVersion.version_number)
        )
    ).scalars().all()
    documents = (
        await db_session.execute(select(KnowledgeDocument))
    ).scalars().all()
    assert document is not None
    assert document.deleted_at is None
    assert document.current_version_id == versions[0].id
    assert len(documents) == 1
    assert [version.status for version in versions] == ["published", "retired"]


@pytest.mark.anyio
async def test_deleted_knowledge_with_new_content_publishes_before_restore(
    app,
    db_session,
    client,
):
    await create_user(db_session, "restore-update-admin", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "restore-update-admin")
    created = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "待恢复政策",
            "kb_type": "policy",
            "content": "## 规则\n旧版静态规则。",
        },
        headers=_auth(token),
    )
    item = created.json()["item"]
    old_version = (
        await db_session.execute(
            select(KnowledgeDocumentVersion).where(
                KnowledgeDocumentVersion.document_id == item["id"]
            )
        )
    ).scalar_one()
    old_parent = (
        await db_session.execute(
            select(KnowledgeParent).where(
                KnowledgeParent.document_version_id == old_version.id
            )
        )
    ).scalar_one()
    old_parent_id = old_parent.id
    old_parent_content = old_parent.content
    old_published_at = old_version.published_at

    deleted = await client.delete(
        f"/api/admin/knowledge/{item['id']}",
        headers=_auth(token),
    )
    assert deleted.status_code == 204

    restored = await client.post(
        "/api/admin/knowledge",
        json={
            "source_key": item["source_key"],
            "title": "待恢复政策",
            "kb_type": "policy",
            "content": "## 规则\n新版静态规则。",
        },
        headers=_auth(token),
    )

    assert restored.status_code == 200
    assert restored.json()["ingestion"]["status"] == "succeeded"
    assert restored.json()["item"]["current_version_number"] == 2

    db_session.expire_all()
    document = await db_session.get(KnowledgeDocument, item["id"])
    versions = (
        await db_session.execute(
            select(KnowledgeDocumentVersion)
            .where(KnowledgeDocumentVersion.document_id == item["id"])
            .order_by(KnowledgeDocumentVersion.version_number)
        )
    ).scalars().all()
    preserved_parent = await db_session.get(KnowledgeParent, old_parent_id)
    assert document is not None
    assert document.deleted_at is None
    assert [version.status for version in versions] == ["retired", "published"]
    assert versions[0].id == old_version.id
    assert versions[0].published_at == old_published_at
    assert preserved_parent is not None
    assert preserved_parent.document_version_id == old_version.id
    assert preserved_parent.content == old_parent_content


@pytest.mark.anyio
async def test_failed_replacement_keeps_deleted_knowledge_hidden(
    app,
    db_session,
    client,
):
    await create_user(db_session, "restore-failure-admin", role="admin")

    class WrongCountEmbeddings(FakeEmbeddings):
        async def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return (await super().embed_documents(texts))[:-1]

    _use_fake_providers(app)
    token = await _login(client, "restore-failure-admin")
    created = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "失败恢复政策",
            "kb_type": "policy",
            "content": "## 规则\n旧版仍然有效。",
        },
        headers=_auth(token),
    )
    item = created.json()["item"]
    deleted = await client.delete(
        f"/api/admin/knowledge/{item['id']}",
        headers=_auth(token),
    )
    assert deleted.status_code == 204
    _use_providers(app, WrongCountEmbeddings())

    failed = await client.post(
        "/api/admin/knowledge",
        json={
            "source_key": item["source_key"],
            "title": "失败恢复政策",
            "kb_type": "policy",
            "content": "## 规则\n新版发布必须失败。",
        },
        headers=_auth(token),
    )

    assert failed.status_code == 200
    assert failed.json()["ingestion"]["status"] == "failed"
    assert (await client.get("/api/admin/knowledge", headers=_auth(token))).json() == []
    db_session.expire_all()
    document = await db_session.get(KnowledgeDocument, item["id"])
    versions = (
        await db_session.execute(
            select(KnowledgeDocumentVersion)
            .where(KnowledgeDocumentVersion.document_id == item["id"])
            .order_by(KnowledgeDocumentVersion.version_number)
        )
    ).scalars().all()
    assert document is not None
    assert document.deleted_at is not None
    assert [version.status for version in versions] == ["published", "failed"]


@pytest.mark.anyio
async def test_knowledge_file_preview_is_editable_and_confirmation_uses_markdown(
    app,
    db_session,
    client,
):
    await create_user(db_session, "preview-admin", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "preview-admin")

    content_bytes = "# 常见问题\n\n原始说明。".encode("utf-8")
    preview = await client.post(
        "/api/admin/knowledge/preview",
        files={
            "file": (
                "faq.markdown",
                content_bytes,
                "text/markdown",
            )
        },
        headers=_auth(token),
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["title"] == "faq"
    assert body["source_kind"] == "text"
    assert body["original_filename"] == "faq.markdown"
    assert body["content"] == "# 常见问题\n\n原始说明。"
    assert body["source_key"] == (
        "file-" + hashlib.sha256(content_bytes).hexdigest()
    )
    assert body["warnings"] == []
    assert (
        await db_session.execute(select(KnowledgeDocument))
    ).scalars().all() == []

    edited = body["content"] + "\n\n人工补充：以商品页公示为准。"
    confirmed = await client.post(
        "/api/admin/knowledge",
        json={
            "source_key": body["source_key"],
            "title": body["title"],
            "kb_type": "policy",
            "content": edited,
            "source_kind": body["source_kind"],
            "original_filename": body["original_filename"],
        },
        headers=_auth(token),
    )
    assert confirmed.status_code == 200
    item = confirmed.json()["item"]
    assert item["source_key"] == body["source_key"]
    assert item["origin"] == "text"

    detail = await client.get(
        f"/api/admin/knowledge/{item['id']}",
        headers=_auth(token),
    )
    assert detail.status_code == 200
    assert detail.json()["content"] == edited


@pytest.mark.anyio
async def test_preview_and_upsert_auto_detect_metadata_from_final_markdown(
    app,
    db_session,
    client,
):
    await create_user(db_session, "metadata-admin", role="admin")
    _use_fake_providers(app)
    token = await _login(client, "metadata-admin")
    content = (
        "# 售后规则\n\n"
        "适用范围：官方渠道购买的商品\n"
        "生效日期：2026年11月20日"
    )

    preview = await client.post(
        "/api/admin/knowledge/preview",
        files={
            "file": (
                "after-sale.md",
                content.encode("utf-8"),
                "text/markdown",
            )
        },
        headers=_auth(token),
    )

    assert preview.status_code == 200
    assert preview.json()["applicability"] == "官方渠道购买的商品"
    assert preview.json()["effective_date"] == "2026-11-20"

    edited = content.replace(
        "官方渠道购买的商品",
        "通过官方桌面端登记的商品",
    ).replace("2026年11月20日", "2026年11月22日")
    confirmed = await client.post(
        "/api/admin/knowledge",
        json={
            "source_key": preview.json()["source_key"],
            "title": preview.json()["title"],
            "kb_type": "policy",
            "applicability": "客户端伪造的范围",
            "effective_date": "2099-01-01",
            "content": edited,
            "source_kind": "text",
            "original_filename": preview.json()["original_filename"],
        },
        headers=_auth(token),
    )

    assert confirmed.status_code == 200
    item = confirmed.json()["item"]
    assert item["applicability"] == "通过官方桌面端登记的商品"
    assert item["effective_date"] == "2026-11-22"


@pytest.mark.anyio
async def test_docx_and_text_pdf_are_converted_to_markdown(
    app,
    db_session,
    client,
):
    await create_user(db_session, "admin1", role="admin")
    token = await _login(client, "admin1")

    docx = await client.post(
        "/api/admin/knowledge/preview",
        files={
            "file": (
                "guide.docx",
                _docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        headers=_auth(token),
    )
    assert docx.status_code == 200
    assert docx.json()["source_kind"] == "word"
    assert "# 安装说明" in docx.json()["content"]
    assert "请先连接官方电源。" in docx.json()["content"]
    assert "| 步骤 | 操作 |" in docx.json()["content"]

    pdf = await client.post(
        "/api/admin/knowledge/preview",
        files={"file": ("warranty.pdf", _text_pdf_bytes(), "application/pdf")},
        headers=_auth(token),
    )
    assert pdf.status_code == 200
    assert pdf.json()["source_kind"] == "pdf"
    assert pdf.json()["page_count"] == 1
    assert "original invoice" in pdf.json()["content"]


@pytest.mark.anyio
async def test_scanned_pdf_and_image_use_ocr_provider(
    app,
    db_session,
    client,
):
    await create_user(db_session, "admin1", role="admin")
    token = await _login(client, "admin1")
    app.dependency_overrides[get_ocr_provider] = lambda: StubOCR()

    scanned = await client.post(
        "/api/admin/knowledge/preview",
        files={"file": ("scan.pdf", _image_pdf_bytes(), "application/pdf")},
        headers=_auth(token),
    )
    assert scanned.status_code == 200
    assert scanned.json()["source_kind"] == "pdf"
    assert "固定安装步骤" in scanned.json()["content"]
    assert scanned.json()["warnings"]

    image = await client.post(
        "/api/admin/knowledge/preview",
        files={"file": ("steps.png", _image_bytes(), "image/png")},
        headers=_auth(token),
    )
    assert image.status_code == 200
    assert image.json()["source_kind"] == "image"
    assert image.json()["page_count"] == 1
    assert "固定安装步骤" in image.json()["content"]
    assert image.json()["warnings"]


@pytest.mark.anyio
async def test_file_preview_rejects_legacy_word_and_spoofed_content(
    app,
    db_session,
    client,
):
    await create_user(db_session, "admin1", role="admin")
    token = await _login(client, "admin1")
    legacy = await client.post(
        "/api/admin/knowledge/preview",
        files={"file": ("legacy.doc", b"not-a-docx", "application/msword")},
        headers=_auth(token),
    )
    assert legacy.status_code == 415
    assert ".docx" in legacy.json()["detail"]

    spoofed = await client.post(
        "/api/admin/knowledge/preview",
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
        headers=_auth(token),
    )
    assert spoofed.status_code == 422
    assert "文件内容" in spoofed.json()["detail"]


@pytest.mark.anyio
async def test_dynamic_sql_facts_are_rejected_without_vector_residue(
    app,
    db_session,
    client,
):
    await create_user(db_session, "dynamic-admin", role="admin")
    customer = await create_user(db_session, "dynamic-owner", role="customer")
    product = await create_product(
        db_session,
        sku="DYNAMIC-SKU-9527",
        name="动态价格商品",
        price="9527.43",
        stock=2468,
    )
    order = await create_order(
        db_session,
        user_id=customer.id,
        order_no="DYNAMIC-ORDER-778899",
        total="9527.43",
        logistics=[
            {
                "status": "运输中",
                "location": "上海虹桥分拨中心",
                "description": "快件已到达上海虹桥分拨中心",
            }
        ],
    )
    after_sale = await create_after_sale(
        db_session,
        order.id,
        refund_amount="321.09",
        reason="动态售后原因",
    )
    _use_fake_providers(app)
    token = await _login(client, "dynamic-admin")
    logistics = (
        await db_session.execute(select(LogisticsEvent))
    ).scalar_one()
    payload_content = (
        "## 实时业务快照\n"
        f"价格：{product.price} 元。\n"
        f"库存：{product.stock} 件。\n"
        f"订单号：{order.order_no}。\n"
        f"物流：{logistics.description}。\n"
        f"售后状态：{after_sale.status}。"
    )

    response = await client.post(
        "/api/admin/knowledge",
        json={
            "title": "不应入库的实时事实",
            "kb_type": "product",
            "content": payload_content,
        },
        headers=_auth(token),
    )

    assert response.status_code == 200
    ingestion = response.json()["ingestion"]
    assert ingestion["status"] == "failed"
    assert ingestion["chunks_indexed"] == 0
    assert "dynamic" in ingestion["failure_reason"].lower()

    stored_product = (
        await db_session.execute(
            select(Product).where(Product.id == product.id)
        )
    ).scalar_one()
    stored_order = (
        await db_session.execute(select(Order).where(Order.id == order.id))
    ).scalar_one()
    stored_after_sale = (
        await db_session.execute(
            select(AfterSale).where(AfterSale.id == after_sale.id)
        )
    ).scalar_one()
    assert stored_product.price == Decimal("9527.43")
    assert stored_product.stock == 2468
    assert stored_order.order_no == "DYNAMIC-ORDER-778899"
    assert stored_after_sale.refund_amount == Decimal("321.09")

    for model in (
        KnowledgeDocument,
        KnowledgeDocumentVersion,
        KnowledgeParent,
        KnowledgeChunk,
    ):
        rows = (await db_session.execute(select(model))).scalars().all()
        assert rows == []


@pytest.mark.anyio
async def test_admin_knowledge_routes_reject_unauthenticated_and_customer_requests(
    db_session,
    client,
):
    await create_user(db_session, "knowledge-customer", role="customer")
    token = await _login(client, "knowledge-customer")
    payload = {
        "title": "不允许顾客添加的知识",
        "kb_type": "policy",
        "content": "## 权限\n顾客不能添加知识。",
    }
    requests = (
        ("get", "/api/admin/knowledge", None),
        ("get", "/api/admin/knowledge/1", None),
        ("post", "/api/admin/knowledge", payload),
        ("post", "/api/admin/knowledge/preview", None),
        ("delete", "/api/admin/knowledge/1", None),
    )

    for method, path, body in requests:
        request_kwargs = {"files": {"file": ("faq.txt", b"FAQ")}} if "preview" in path else {"json": body}
        unauthenticated = await client.request(method, path, **request_kwargs)
        forbidden = await client.request(
            method,
            path,
            headers=_auth(token),
            **request_kwargs,
        )
        assert unauthenticated.status_code == 401
        assert forbidden.status_code == 403
