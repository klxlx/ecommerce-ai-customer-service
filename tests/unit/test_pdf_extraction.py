"""PDF extraction contracts against the registered official documents."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.rag.pdf_extraction import extract_pdf, load_pdf_sources

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "data" / "knowledge" / "pdf_sources.json"


def _source(file_name: str):
    return next(
        source
        for source in load_pdf_sources(REGISTRY_PATH)
        if source.file_name == file_name
    )


def test_huawei_outline_extraction_preserves_pages_and_section_paths():
    source = _source(
        "HUAWEI Pura 70 Pro 用户指南-(HBN-AL00,HarmonyOS 5.0_01,zh-cn).pdf"
    )

    document = extract_pdf(source, ROOT / "data" / "knowledge" / "pdf_sources")

    assert document.checksum == source.checksum
    assert document.page_count == source.page_count
    assert document.source_id == source.source_id
    assert len(document.parents) >= 100
    assert min(parent.page_start for parent in document.parents) >= 4
    assert all(
        parent.page_start <= parent.page_end <= source.page_count
        for parent in document.parents
    )
    assert all(parent.section_path for parent in document.parents)
    assert any("实况窗" in parent.section_path for parent in document.parents)
    assert any("实况窗" in parent.content for parent in document.parents)
    assert all("目录" not in parent.section_path for parent in document.parents)


def test_jd_policy_extraction_splits_chapters_sections_and_articles():
    source = _source("京东开放平台售后服务管理规则.pdf")

    document = extract_pdf(source, ROOT / "data" / "knowledge" / "pdf_sources")

    assert len(document.parents) >= 20
    assert all(
        parent.page_start <= parent.page_end <= source.page_count
        for parent in document.parents
    )
    assert any(
        "第一章 概述" in parent.section_path for parent in document.parents
    )
    assert any(
        "第一节 商品售后签收标准" in parent.section_path
        for parent in document.parents
    )
    assert any("第一条" in parent.content for parent in document.parents)
    assert any("第三十六条" in parent.content for parent in document.parents)


def test_all_registered_pdfs_extract_with_traceable_metadata():
    sources = load_pdf_sources(REGISTRY_PATH)

    for source in sources:
        document = extract_pdf(source, ROOT / "data" / "knowledge" / "pdf_sources")
        assert document.checksum == source.checksum
        assert document.page_count == source.page_count
        assert document.parents
        assert all(parent.content.strip() for parent in document.parents)
        assert all(parent.section_path for parent in document.parents)
        assert all(
            1 <= parent.page_start <= parent.page_end <= source.page_count
            for parent in document.parents
        )


def test_extractor_rejects_checksum_mismatch():
    source = _source("京东开放平台闪电退款服务规则.pdf")
    tampered = replace(source, checksum="0" * 64)

    with pytest.raises(ValueError, match="checksum"):
        extract_pdf(tampered, ROOT / "data" / "knowledge" / "pdf_sources")
