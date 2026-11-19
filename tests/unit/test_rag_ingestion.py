"""Unit tests for deterministic parent/child chunking and dynamic-field guards."""

from __future__ import annotations

import pytest

from app.rag.indexer import _prepare_document
from app.rag.pdf_extraction import ExtractedDocument, ExtractedParent
from app.rag.chunking import assert_no_dynamic_fields, split_children, split_parents

MAX_PARENT_CHARS = 4000


def test_split_parents_on_headings():
    text = "导语内容。\n## 使用方法\n第一步。\n## 注意事项\n注意安全。"
    parents = split_parents(text)
    headings = [p.heading for p in parents]
    assert headings == ["", "使用方法", "注意事项"]
    assert parents[1].content == "第一步。"


def test_split_parents_single_block_without_headings():
    parents = split_parents("只有一段内容。")
    assert len(parents) == 1
    assert parents[0].heading == ""


def test_long_parent_without_headings_is_split_into_bounded_blocks():
    text = "\n\n".join(f"第 {index:04d} 段：" + "规则说明。" * 120 for index in range(12))

    parents = split_parents(text)

    assert len(parents) > 1
    assert all(len(parent.content) <= MAX_PARENT_CHARS for parent in parents)
    assert all(parent.heading == "" for parent in parents)
    assert "\n\n".join(parent.content for parent in parents) == text


def test_long_parent_under_heading_is_split_and_keeps_heading():
    body = "\n\n".join("商品使用说明。" * 120 for _ in range(12))
    text = f"## 使用说明\n{body}"

    parents = split_parents(text)

    assert len(parents) > 1
    assert all(len(parent.content) <= MAX_PARENT_CHARS for parent in parents)
    assert all(parent.heading == "使用说明" for parent in parents)
    assert "\n\n".join(parent.content for parent in parents) == body


def test_pdf_extraction_parents_are_bounded_before_indexing():
    body = "\n\n".join("PDF 规则说明。" * 120 for _ in range(12))
    document = ExtractedDocument(
        source_id="pdf-limit",
        file_name="limit.pdf",
        title="超长 PDF",
        kb_type="policy",
        applicability="",
        effective_date=None,
        checksum="a" * 64,
        page_count=1,
        extraction_strategy="jd_clauses",
        parents=(
            ExtractedParent(
                index=0,
                content=body,
                section_path="第一章 > 第一节",
                page_start=1,
                page_end=1,
            ),
        ),
    )

    prepared = _prepare_document(document, chunk_size=400, overlap=80)

    extracted_parents = [parent for parent, _ in prepared.parents]
    assert len(extracted_parents) > 1
    assert all(len(parent.content) <= MAX_PARENT_CHARS for parent in extracted_parents)
    assert {parent.section_path for parent in extracted_parents} == {
        "第一章 > 第一节"
    }
    assert [parent.index for parent in extracted_parents] == list(
        range(len(extracted_parents))
    )


def test_split_children_with_overlap():
    content = "字" * 100
    chunks = split_children(content, chunk_size=30, overlap=10)
    assert len(chunks) > 1
    # Overlap: chunk[i] ends where chunk[i+1] starts, offset by step.
    assert chunks[0][-10:] == chunks[1][:10]


def test_split_children_short_content_single_chunk():
    assert split_children("短", chunk_size=30, overlap=10) == ["短"]


def test_assert_no_dynamic_fields_accepts_static_knowledge():
    assert_no_dynamic_fields("本产品支持主动降噪与蓝牙连接。")


def test_assert_no_dynamic_fields_rejects_price_placeholder():
    with pytest.raises(ValueError):
        assert_no_dynamic_fields("当前售价为 {{price}} 元。")


def test_assert_no_dynamic_fields_rejects_stock_literal():
    with pytest.raises(ValueError):
        assert_no_dynamic_fields("库存：100 件。")
