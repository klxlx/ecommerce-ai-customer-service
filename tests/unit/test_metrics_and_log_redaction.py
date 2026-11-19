"""Runtime metrics and safe logging tests."""

from __future__ import annotations

import logging

from app.core.logging import RedactingFormatter, redact_sensitive
from app.core.metrics import MetricsRegistry


def test_metrics_registry_serializes_counters_and_latency():
    metrics = MetricsRegistry()
    metrics.increment("chat.request.total")
    metrics.increment("tool.call.total", tool="order", outcome="success")
    metrics.observe("chat.latency_ms", 12.5)
    metrics.observe("chat.latency_ms", 7.5)

    snapshot = metrics.snapshot()

    assert snapshot["counters"]["chat.request.total"]["value"] == 1
    assert snapshot["counters"]["tool.call.total"][
        "tool=order,outcome=success"
    ] == 1
    assert snapshot["timings"]["chat.latency_ms"] == {
        "count": 2,
        "sum_ms": 20.0,
        "avg_ms": 10.0,
    }


def test_redaction_removes_credentials_messages_and_order_privacy():
    raw = (
        "password=pw123 token=abc123 api_key=sk-secret "
        "Authorization: Bearer bearer-secret "
        "message=我要退款订单 ORD-123456789 phone=13800138000 "
        "address=上海市某路1号 "
        "postgresql://user:db-secret@localhost/db"
    )

    cleaned = redact_sensitive(raw)

    for secret in (
        "pw123",
        "abc123",
        "sk-secret",
        "bearer-secret",
        "我要退款订单",
        "ORD-123456789",
        "13800138000",
        "上海市某路1号",
        "db-secret",
    ):
        assert secret not in cleaned
    assert "[REDACTED]" in cleaned


def test_redacting_formatter_sanitizes_rendered_log_record():
    formatter = RedactingFormatter("%(levelname)s %(message)s")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="token=%s content=%s",
        args=("top-secret", "完整用户消息"),
        exc_info=None,
    )

    rendered = formatter.format(record)

    assert "top-secret" not in rendered
    assert "完整用户消息" not in rendered
    assert "[REDACTED]" in rendered

