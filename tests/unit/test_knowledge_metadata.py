"""Automatic applicability and effective-date detection for knowledge."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.knowledge_metadata import detect_knowledge_metadata


@pytest.mark.parametrize(
    ("content", "expected_scope", "expected_date"),
    [
        (
            "## 政策说明\n适用范围：官方渠道购买的手机\n"
            "生效日期：2026年11月1日",
            "官方渠道购买的手机",
            date(2026, 11, 1),
        ),
        (
            "本政策仅适用于通过官方桌面端登记的商品。\n"
            "自2026-11-02起施行。",
            "通过官方桌面端登记的商品",
            date(2026, 11, 2),
        ),
        (
            "仅适用于京东自营商品\n自2026年11月3日起生效",
            "京东自营商品",
            date(2026, 11, 3),
        ),
    ],
)
def test_detects_scope_and_effective_date_from_policy_text(
    content: str,
    expected_scope: str,
    expected_date: date,
) -> None:
    metadata = detect_knowledge_metadata(content)

    assert metadata.applicability == expected_scope
    assert metadata.effective_date == expected_date


def test_returns_empty_metadata_when_text_has_no_supported_fields() -> None:
    metadata = detect_knowledge_metadata(
        "## 安装\n请先连接官方电源，再长按电源键开机。"
    )

    assert metadata.applicability == ""
    assert metadata.effective_date is None


def test_invalid_or_impossible_dates_are_left_empty() -> None:
    metadata = detect_knowledge_metadata(
        "适用范围：官方渠道\n生效日期：2026年13月40日"
    )

    assert metadata.applicability == "官方渠道"
    assert metadata.effective_date is None


def test_registered_pdf_metadata_takes_priority_over_extracted_text() -> None:
    checksum = (
        "16a74acadaea2f1fbabbecac14ca116df6a11cd00d1b8d11a8f2a6134a0a2190"
    )

    metadata = detect_knowledge_metadata(
        "适用范围：错误的手工内容\n生效日期：2099年1月1日",
        source_key=f"file-{checksum}",
    )

    assert metadata.applicability == "京东开放平台7天无理由退货规则"
    assert metadata.effective_date == date(2024, 5, 8)
