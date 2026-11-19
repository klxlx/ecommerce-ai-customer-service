"""Chat service: orchestrate the LangGraph agent and persist the turn."""

from __future__ import annotations

from time import perf_counter

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProviderError
from app.core.metrics import MetricsRegistry
from app.db.models import User
from app.db.repositories import chat as chat_repo
from app.graph.builder import Container, build_graph
from app.graph.state import AgentState
from app.schemas.chat import ChatResponse
from app.services import handoff as handoff_service

DEFAULT_CONVERSATION_TITLE = "智能客服"
PROVIDER_FAILURE_ANSWER = (
    "抱歉，智能客服暂时不可用，已为您转接人工客服，请稍候。"
)


async def run_chat(
    session: AsyncSession,
    user: User,
    container: Container,
    conversation_id: int,
    message: str,
    *,
    metrics: MetricsRegistry | None = None,
) -> ChatResponse | None:
    """Run one assistant turn for ``message`` in an owned conversation."""
    conversation = await chat_repo.get_conversation_owned(
        session, conversation_id, user.id
    )
    if conversation is None:
        return None

    metrics = metrics or container.metrics
    active_handoff = await handoff_service.get_active_ticket(
        session, conversation.id
    )
    if active_handoff is not None:
        user_message = await chat_repo.add_message(
            session, conversation.id, "user", message
        )
        await session.commit()
        if metrics:
            metrics.increment("chat.request.total")
            metrics.increment("chat.success.total")
        return ChatResponse(
            answer="消息已发送给人工客服，请稍候。",
            sources=[],
            handoff={
                "id": active_handoff.id,
                "status": active_handoff.status,
                "reason": "",
            },
            conversation_id=conversation.id,
            message_id=user_message.id,
        )

    prior_messages = await chat_repo.list_messages(session, conversation.id)
    history = [{"role": m.role, "content": m.content} for m in prior_messages]

    # Persist the user message first.
    await chat_repo.add_message(session, conversation.id, "user", message)
    if metrics:
        metrics.increment("chat.request.total")

    state: AgentState = {
        "user_message": message,
        "user_id": user.id,
        "history": history,
        "slots": conversation.slots or {},
        "sources": [],
        "tool_evidence": {},
        "chit_chat_streak": conversation.chit_chat_streak,
        "clarification_attempts": conversation.clarification_attempts,
        "session": session,
        "chat": container.chat,
        "providers": container.providers,
        "agents": container.agents,
        "weather": container.weather,
        "local_time": container.local_time,
    }

    graph = build_graph(container)
    started_at = perf_counter()
    provider_failed = False
    try:
        result = await graph.ainvoke(state)
    except ProviderError as error:
        provider_failed = True
        _record_provider_failure(metrics, error)
        result = _provider_failure_result(conversation.slots or {}, error)
    finally:
        if metrics:
            metrics.observe(
                "chat.latency_ms", (perf_counter() - started_at) * 1000
            )

    response = await _persist_result(
        session,
        user,
        conversation,
        message,
        result,
    )
    if metrics:
        metrics.increment(
            "chat.failure.total" if provider_failed else "chat.success.total"
        )
        _record_turn_metrics(metrics, result)
    return response


async def _persist_result(
    session: AsyncSession,
    user: User,
    conversation,
    message: str,
    result: dict,
) -> ChatResponse:
    intent = result.get("intent")
    answer = result.get("answer") or "抱歉，我暂时无法处理您的请求。"
    sources = result.get("sources") or []
    tool_evidence = result.get("tool_evidence") or {}
    handoff_payload = result.get("handoff")
    after_sale_context = result.get("after_sale_context")
    slots = result.get("slots")
    if slots is None:
        slots = conversation.slots or {}
    # LangGraph carries input state through untouched terminal nodes. Do not
    # treat those inherited values as fresh counter updates.
    chit_chat_streak = (
        result.get("chit_chat_streak", 0) if intent == "chit_chat" else 0
    )
    clarification_attempts = (
        result.get("clarification_attempts", 0)
        if intent == "clarification"
        else 0
    )

    # Persist the agent run trace.
    trace = {
        "intent": intent,
        "slots": slots,
        "sources": sources,
        "tool_evidence": tool_evidence,
        "handoff_reason": (
            handoff_payload.get("reason") if handoff_payload else None
        ),
    }
    agent_run = await chat_repo.create_agent_run(session, conversation.id, intent, trace)
    await chat_repo.create_tool_call_log(
        session,
        agent_run.id,
        tool_name=_tool_name(intent, tool_evidence),
        arguments=slots,
        result=tool_evidence,
    )

    assistant_message = await chat_repo.add_message(
        session,
        conversation.id,
        "assistant",
        answer,
        intent=intent,
        sources=sources,
        trace_id=agent_run.id,
        after_sale_context=after_sale_context,
    )

    await chat_repo.update_conversation_state(
        session,
        conversation,
        slots=slots,
        chit_chat_streak=chit_chat_streak,
        clarification_attempts=clarification_attempts,
    )
    if conversation.title == DEFAULT_CONVERSATION_TITLE:
        conversation.title = message[:20] or DEFAULT_CONVERSATION_TITLE

    handoff_out = None
    if handoff_payload:
        ticket = await handoff_service.create_ticket(
            session,
            conversation_id=conversation.id,
            user_id=user.id,
            summary=handoff_payload.get("summary", message),
            slots=handoff_payload.get("slots", {}),
            recent_messages=[{"role": "user", "content": message}],
            evidence=handoff_payload.get("evidence", {}),
        )
        handoff_out = {
            "id": ticket.id,
            "status": ticket.status,
            "reason": handoff_payload.get("reason", ""),
        }

    await session.commit()

    return ChatResponse(
        answer=answer,
        intent=intent,
        sources=sources,
        trace_id=agent_run.id,
        handoff=handoff_out,
        after_sale=after_sale_context,
        conversation_id=conversation.id,
        message_id=assistant_message.id,
    )


def _provider_failure_result(slots: dict, error: ProviderError) -> dict:
    evidence = error.as_evidence()
    return {
        "intent": "handoff",
        "answer": PROVIDER_FAILURE_ANSWER,
        "sources": [],
        "slots": slots,
        "tool_evidence": {"provider_failure": evidence},
        "handoff": {
            "reason": "provider_failure",
            "summary": "智能客服 Provider 最终失败，需人工继续处理",
            "slots": slots,
            "evidence": {"provider_failure": evidence},
        },
    }


def _record_provider_failure(
    metrics: MetricsRegistry | None, error: ProviderError
) -> None:
    if metrics and not error.degraded_recorded:
        metrics.increment(
            "provider.degraded.total",
            provider=error.provider,
            reason=error.category,
        )
        error.degraded_recorded = True


def _record_turn_metrics(metrics: MetricsRegistry, result: dict) -> None:
    intent = result.get("intent")
    evidence = result.get("tool_evidence") or {}
    after_sale = result.get("after_sale_context")
    handoff = result.get("handoff")

    if "provider_failure" not in evidence:
        metrics.increment(
            "tool.call.total",
            tool=_tool_name(intent, evidence),
            outcome=_tool_outcome(intent, evidence, after_sale),
        )

    if isinstance(after_sale, dict):
        status = after_sale.get("status")
        if status in {
            "has_records",
            "no_records",
            "not_available",
            "query_error",
        }:
            metrics.increment("after_sale.query.total", outcome=status)

    threshold = evidence.get("threshold")
    if isinstance(threshold, dict) and threshold.get("applied") is True:
        metrics.increment(
            "after_sale.refund_threshold.total",
            outcome=(
                "exceeded" if threshold.get("exceeded") else "within_limit"
            ),
        )

    if handoff:
        metrics.increment(
            "handoff.created.total",
            reason=_handoff_reason(handoff, evidence),
        )


def _tool_outcome(
    intent: str | None,
    evidence: dict,
    after_sale_context: dict | None,
) -> str:
    if isinstance(after_sale_context, dict):
        if after_sale_context.get("status") == "query_error":
            return "failure"
    if intent == "handoff" and evidence and "provider_failure" not in evidence:
        return "failure"
    return "success"


def _handoff_reason(handoff: dict, evidence: dict) -> str:
    threshold = evidence.get("threshold")
    if (
        isinstance(threshold, dict)
        and threshold.get("applied") is True
        and threshold.get("exceeded") is True
    ):
        return "threshold_exceeded"

    reason = str(handoff.get("reason") or "")
    stable = {
        "provider_failure",
        "threshold_exceeded",
        "tool_failure",
        "user_request",
        "clarification_failed",
        "knowledge_unavailable",
        "other",
    }
    if reason in stable:
        return reason
    if reason == "用户请求人工服务":
        return "user_request"
    if reason in {"工具查询失败", "多次澄清失败"}:
        return (
            "tool_failure"
            if reason == "工具查询失败"
            else "clarification_failed"
        )
    return "other"


def _tool_name(intent: str | None, tool_evidence: dict) -> str:
    if "after_sale" in tool_evidence:
        return "after_sale"
    if "product" in tool_evidence:
        return "catalog"
    if "order" in tool_evidence:
        return "order"
    if "knowledge" in tool_evidence or "kb_type" in tool_evidence:
        return "knowledge"
    return intent or "none"
