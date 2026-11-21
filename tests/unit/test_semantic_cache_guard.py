"""Semantic cache should_lookup guard: static FAQ vs personal/dynamic."""

from __future__ import annotations

import pytest

from app.services.semantic_cache import query_variants, should_lookup


@pytest.mark.parametrize(
    "message",
    [
        "签收后多久可以申请无理由退货？",
        "退款多久到账？",
        "怎么申请退货？",
        "这款耳机蓝牙版本是多少？",
        "净化器适用面积多大？",
        "物流长时间不更新怎么办？",
    ],
)
def test_static_faq_questions_are_cacheable(message: str) -> None:
    assert should_lookup(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "我的订单退款进度到哪了？",
        "我的订单物流到哪了？",
        "快递单号SF1234567890",
        "我要退货",
        "我要投诉你们",
        "这个商品多少钱？",
        "有货吗？",
    ],
)
def test_personal_or_dynamic_questions_never_use_cache(message: str) -> None:
    assert should_lookup(message) is False


def test_demonstrative_prefix_is_removed_for_faq_lookup() -> None:
    assert query_variants("这款耳机蓝牙版本是多少？") == ["耳机蓝牙版本是多少？"]


def test_common_refund_phrasing_uses_curated_refund_faq() -> None:
    assert query_variants("退款多久到账？") == ["退货退款多久到账？"]
    assert query_variants("钱什么时候退回来") == ["退货退款多久到账？"]
