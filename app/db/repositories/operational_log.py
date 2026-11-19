"""Read-only queries for the administrator operational-log view."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    and_,
    case,
    cast,
    false,
    func,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentRun, ChatMessage, Conversation, ToolCallLog, User
from app.schemas.operational_log import OperationalLogQuery

_AFTER_SALE_STATUSES = {
    "has_records",
    "no_records",
    "not_available",
    "query_error",
}
_HANDOFF_REASONS = {
    "provider_failure",
    "threshold_exceeded",
    "tool_failure",
    "user_request",
    "clarification_failed",
    "other",
}
_HANDOFF_REASON_ALIASES = {
    "用户请求人工服务": "user_request",
    "工具查询失败": "tool_failure",
    "多次澄清失败": "clarification_failed",
}


def _trace_json():
    return cast(AgentRun.trace, JSONB)


def _latest_tool_name():
    return (
        select(ToolCallLog.tool_name)
        .where(ToolCallLog.agent_run_id == AgentRun.id)
        .order_by(ToolCallLog.created_at.desc(), ToolCallLog.id.desc())
        .limit(1)
        .scalar_subquery()
    )


def _latest_after_sale_status():
    return (
        select(ChatMessage.after_sale_context["status"].as_string())
        .where(ChatMessage.trace_id == AgentRun.id)
        .order_by(ChatMessage.id.desc())
        .limit(1)
        .scalar_subquery()
    )


def _source_count_expression():
    return func.coalesce(
        func.jsonb_array_length(_trace_json()["sources"]),
        0,
    )


def _threshold_boolean_expression(name: str):
    return func.coalesce(
        _trace_json()["tool_evidence"]["threshold"][name].as_boolean(),
        false(),
    )


def _handoff_reason_expression():
    return func.nullif(_trace_json()["handoff_reason"].as_string(), "")


def _provider_failure_expression():
    return func.coalesce(
        func.jsonb_exists(_trace_json()["tool_evidence"], "provider_failure"),
        false(),
    )


def _has_trace_expression():
    return func.coalesce(
        _trace_json().is_distinct_from(cast({}, JSONB)),
        false(),
    )


def _run_status_expression(after_sale_status):
    failed = or_(
        _provider_failure_expression(),
        after_sale_status == "query_error",
    )
    return case(
        (failed, "failed"),
        (_handoff_reason_expression().is_not(None), "handoff"),
        (_has_trace_expression(), "success"),
        else_="unknown",
    )


def _apply_filters(stmt, query: OperationalLogQuery):
    after_sale_status = _latest_after_sale_status()
    source_count = _source_count_expression()

    if query.intent is not None:
        stmt = stmt.where(AgentRun.intent == query.intent)
    if query.tool_name is not None:
        stmt = stmt.where(_latest_tool_name() == query.tool_name)
    if query.retrieval_result == "hit":
        stmt = stmt.where(source_count > 0)
    elif query.retrieval_result == "empty":
        stmt = stmt.where(
            AgentRun.intent == "knowledge",
            source_count == 0,
        )
    elif query.retrieval_result == "not_applicable":
        stmt = stmt.where(
            AgentRun.intent.is_distinct_from("knowledge"),
            source_count == 0,
        )
    if query.after_sale_status is not None:
        stmt = stmt.where(after_sale_status == query.after_sale_status)
    if query.threshold_applied is not None:
        stmt = stmt.where(
            _threshold_boolean_expression("applied")
            == query.threshold_applied
        )
    if query.threshold_exceeded is not None:
        stmt = stmt.where(
            _threshold_boolean_expression("exceeded")
            == query.threshold_exceeded
        )
    if query.has_handoff is not None:
        stmt = stmt.where(
            _handoff_reason_expression().is_not(None)
            == query.has_handoff
        )
    if query.run_status is not None:
        stmt = stmt.where(
            _run_status_expression(after_sale_status) == query.run_status
        )
    if query.user_id is not None:
        stmt = stmt.where(Conversation.user_id == query.user_id)
    if query.conversation_id is not None:
        stmt = stmt.where(AgentRun.conversation_id == query.conversation_id)
    if query.start_at is not None:
        stmt = stmt.where(AgentRun.created_at >= query.start_at)
    if query.end_at is not None:
        stmt = stmt.where(AgentRun.created_at <= query.end_at)
    return stmt


async def list_operational_logs(
    session: AsyncSession,
    query: OperationalLogQuery,
) -> tuple[list[dict[str, Any]], int]:
    after_sale_status = _latest_after_sale_status()
    base = (
        select(
            AgentRun.id.label("run_id"),
            AgentRun.conversation_id,
            Conversation.user_id,
            AgentRun.intent,
            AgentRun.trace,
            AgentRun.created_at,
            _latest_tool_name().label("tool_name"),
            after_sale_status.label("after_sale_status"),
        )
        .join(Conversation, Conversation.id == AgentRun.conversation_id)
        .join(User, User.id == Conversation.user_id)
    )
    filtered = _apply_filters(base, query)
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(filtered.subquery())
            )
        ).scalar_one()
    )
    rows = (
        await session.execute(
            filtered.order_by(
                AgentRun.created_at.desc(),
                AgentRun.id.desc(),
            )
            .limit(query.limit)
            .offset(query.offset)
        )
    ).mappings().all()
    return [_serialize_row(row) for row in rows], total


def _serialize_row(row) -> dict[str, Any]:
    trace = row["trace"] if isinstance(row["trace"], dict) else {}
    sources = _safe_sources(trace.get("sources"))
    source_count = len(trace.get("sources") or [])
    intent = row["intent"]
    retrieval_result = (
        "hit"
        if source_count > 0
        else "empty"
        if intent == "knowledge"
        else "not_applicable"
    )
    threshold = _safe_dict(
        _safe_dict(trace.get("tool_evidence")).get("threshold")
    )
    threshold_applied = threshold.get("applied") is True
    threshold_exceeded = (
        threshold_applied and threshold.get("exceeded") is True
    )
    handoff_reason = _stable_handoff_reason(trace.get("handoff_reason"))
    after_sale_status = row["after_sale_status"]
    if after_sale_status not in _AFTER_SALE_STATUSES:
        after_sale_status = None

    provider_failure = (
        "provider_failure"
        in _safe_dict(trace.get("tool_evidence"))
    )
    run_status = (
        "failed"
        if provider_failure or after_sale_status == "query_error"
        else "handoff"
        if handoff_reason is not None
        else "success"
        if bool(trace)
        else "unknown"
    )
    tool_name = row["tool_name"] or _infer_tool_name(intent, trace)

    return {
        "run_id": row["run_id"],
        "conversation_id": row["conversation_id"],
        "user_id": row["user_id"],
        "intent": intent,
        "tool_name": tool_name,
        "retrieval_result": retrieval_result,
        "source_count": source_count,
        "sources": sources,
        "after_sale_status": after_sale_status,
        "threshold_applied": threshold_applied,
        "threshold_amount": (
            _safe_scalar_text(threshold.get("amount"))
            if threshold_applied
            else None
        ),
        "threshold_limit": (
            _safe_scalar_text(threshold.get("limit"))
            if threshold_applied
            else None
        ),
        "threshold_exceeded": threshold_exceeded,
        "has_handoff": handoff_reason is not None,
        "handoff_reason": handoff_reason,
        "run_status": run_status,
        "created_at": row["created_at"],
    }


def _safe_sources(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    safe = []
    for source in value[:20]:
        if not isinstance(source, dict):
            continue
        title = source.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        safe.append(
            {
                "title": title.strip()[:300],
                "page_start": _safe_page(source.get("page_start")),
                "page_end": _safe_page(source.get("page_end")),
            }
        )
    return safe


def _safe_page(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _safe_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _safe_scalar_text(value: Any) -> str | None:
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def _stable_handoff_reason(value: Any) -> str | None:
    if value is None or value == "":
        return None
    reason = str(value)
    if reason in _HANDOFF_REASONS:
        return reason
    return _HANDOFF_REASON_ALIASES.get(reason, "other")


def _infer_tool_name(intent: str | None, trace: dict) -> str:
    evidence = _safe_dict(trace.get("tool_evidence"))
    if "after_sale" in evidence:
        return "after_sale"
    if "product" in evidence:
        return "catalog"
    if "order" in evidence:
        return "order"
    if "kb_type" in evidence:
        return "knowledge"
    return intent or "none"
