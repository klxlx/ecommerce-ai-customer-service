"""Unit tests for dataset-derived tone, empathy and de-escalation helpers."""

from __future__ import annotations

from app.services.tone import (
    active_handoff_reply,
    deescalation_reply,
    emotion_kind,
    ensure_empathy,
    handoff_reply,
    is_pure_emotion,
    style_cached_answer,
)


def test_threat_and_self_harm_are_classified() -> None:
    assert emotion_kind("我想打人") == "threat"
    assert emotion_kind("我不想活了") == "self_harm"


def test_policy_question_about_complaint_is_not_emotional() -> None:
    assert emotion_kind("投诉规则是什么") == "none"
    assert is_pure_emotion("投诉规则是什么") is False


def test_business_message_with_emotion_is_not_pure_emotion() -> None:
    assert emotion_kind("气死了，我的订单怎么还没到") == "anger"
    assert is_pure_emotion("气死了，我的订单怎么还没到") is False


def test_product_message_with_anger_is_not_pure_emotion() -> None:
    assert is_pure_emotion("烦死了，你们这个耳机蓝牙版本到底是多少？") is False


def test_threat_still_takes_safety_branch_with_business_words() -> None:
    assert is_pure_emotion("再不退款我就打人") is True
    assert is_pure_emotion("我不想活了，订单不要了") is True


def test_product_word_with_garbage_is_not_an_insult() -> None:
    assert emotion_kind("垃圾桶有货吗") == "none"


def test_deescalation_reply_does_not_blame_customer() -> None:
    reply = deescalation_reply("我想打人")
    assert "抱歉" in reply
    assert "安全" in reply
    assert "服务需求太多" not in reply


def test_handoff_reply_soothes_complaint() -> None:
    reply = handoff_reply("我要投诉")
    assert "抱歉" in reply
    assert "转接人工客服" in reply


def test_active_handoff_reply_still_acknowledges_threat() -> None:
    reply = active_handoff_reply("我想打人")
    assert "抱歉" in reply
    assert "安全" in reply
    assert "这条消息已同步给人工客服" in reply


def test_cached_faq_gets_friendly_dataset_style() -> None:
    reply = style_cached_answer(
        "自签收商品之日起 7 天内可申请无理由退货。",
        "policy",
        "签收后多久可以申请无理由退货？",
    )
    assert reply.startswith("亲，您先别着急")
    assert "7 天内" in reply


def test_empathy_is_prepended_to_deterministic_business_answer() -> None:
    reply = ensure_empathy("气死了，我的订单怎么还没到", "您的订单正在运输中。")
    assert reply.startswith("非常抱歉")
    assert "正在运输中" in reply


def test_empathy_is_prepended_even_when_answer_starts_with_friendly_greeting() -> None:
    reply = ensure_empathy("烦死了，耳机怎么连不上", "亲，您好～请先打开蓝牙。")
    assert reply.startswith("非常抱歉")
    assert "亲，您好～请先打开蓝牙。" in reply
