"""Chat service: orchestrate the LangGraph agent and persist the turn."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import perf_counter

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProviderError, ProviderTimeoutError
from app.core.metrics import MetricsRegistry
from app.db.models import User
from app.db.repositories import chat as chat_repo
from app.graph.builder import Container, build_graph
from app.graph.state import AgentState
from app.schemas.chat import ChatResponse
from app.services import handoff as handoff_service
from app.services import semantic_cache as semantic_cache_service
from app.services import sentinel as sentinel_service

DEFAULT_CONVERSATION_TITLE = "智能客服"
NEW_CONVERSATION_GREETING = (
    "你好呀！我是你的智能客服小天，请问有什么能够帮到你的？"
)
PROVIDER_FAILURE_ANSWER = (
    "抱歉，智能客服暂时不可用，已为您转接人工客服，请稍候。"
)
DUPLICATE_TICKET_REPLY = "您反馈的这个问题已经在处理中，请稍候。"


def _llm_history_message(message) -> dict:
    """Convert a persisted message into a provider-safe chat message.

    Human-agent replies are stored with ``role="admin"`` so the UI can label
    them, but OpenAI-compatible providers only accept system/user/assistant
    roles. Without this mapping a single handoff reply makes every later turn
    fail with a 400 error.
    """
    if message.role == "admin":
        return {"role": "assistant", "content": f"[人工客服] {message.content}"}
    if message.role == "system":
        return {"role": "assistant", "content": f"[系统] {message.content}"}
    role = "user" if message.role == "user" else "assistant"
    return {"role": role, "content": message.content}


async def run_chat(
    session: AsyncSession,
    user: User,
    container: Container,
    conversation_id: int,
    message: str,
    *,
    metrics: MetricsRegistry | None = None,
    token_sink: Callable[[str], Awaitable[None]] | None = None,
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
    history = [_llm_history_message(m) for m in prior_messages]

    # Persist the user message first.
    await chat_repo.add_message(session, conversation.id, "user", message)
    if metrics:
        metrics.increment("chat.request.total")

    # Curated FAQ cache is checked before the graph. It never caches dynamic
    # order/after-sale/price answers and never stores model-generated answers.
    cache_hit = await semantic_cache_service.lookup(
        session,
        message,
        container.cache_embeddings or container.providers.embeddings,
        container.settings,
    )
    if cache_hit is not None:
        entry = cache_hit.entry
        intent = (
            "product_knowledge"
            if entry.kb_type == "product"
            else "policy_knowledge"
        )
        agent_run = await chat_repo.create_agent_run(
            session,
            conversation.id,
            intent,
            {
                "semantic_cache": {
                    "hit": True,
                    "entry_id": entry.id,
                    "score": round(cache_hit.score, 4),
                }
            },
        )
        await chat_repo.create_tool_call_log(
            session,
            agent_run.id,
            tool_name="semantic_cache",
            arguments={"question": message},
            result={
                "entry_id": entry.id,
                "score": round(cache_hit.score, 4),
            },
        )
        assistant_message = await chat_repo.add_message(
            session,
            conversation.id,
            "assistant",
            entry.answer,
            intent=intent,
            sources=entry.sources,
            trace_id=agent_run.id,
        )
        if conversation.title == DEFAULT_CONVERSATION_TITLE:
            conversation.title = message[:20] or DEFAULT_CONVERSATION_TITLE
        await session.commit()
        if metrics:
            metrics.increment("semantic_cache.hit.total")
            metrics.increment("chat.success.total")
            metrics.observe("chat.latency_ms", 0)
        return ChatResponse(
            answer=entry.answer,
            intent=intent,
            sources=entry.sources or [],
            cached=True,
            trace_id=agent_run.id,
            conversation_id=conversation.id,
            message_id=assistant_message.id,
        )
    if metrics:
        metrics.increment("semantic_cache.miss.total")

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
        "stream": token_sink,
    }

    graph = build_graph(container)
    started_at = perf_counter()
    provider_failed = False
    try:
        async with asyncio.timeout(container.settings.chat_total_budget_seconds):
            result = await graph.ainvoke(state)
    except TimeoutError:
        provider_failed = True
        error = ProviderTimeoutError("chat", "turn budget exceeded")
        _record_provider_failure(metrics, error)
        result = _provider_failure_result(conversation.slots or {}, error)
    except ProviderError as error:
        provider_failed = True
        _record_provider_failure(metrics, error)
        result = _provider_failure_result(conversation.slots or {}, error)
    finally:
        if metrics:
            metrics.observe(
                "chat.latency_ms", (perf_counter() - started_at) * 1000
            )

    # The sentinel runs outside the graph. It only replaces unsupported /
    # policy-promise answers; any guard failure fails open.
    verdict = await sentinel_service.check_answer(
        container,
        question=message,
        answer=str(result.get("answer") or ""),
        intent=result.get("intent"),
        sources=result.get("sources"),
        tool_evidence=result.get("tool_evidence"),
        grounding_context=str(result.get("grounding_context") or ""),
    )
    result["sentinel"] = verdict.trace()
    if verdict.flagged:
        result = dict(result)
        result["answer"] = sentinel_service.SENTINEL_FALLBACK_ANSWER
        if metrics:
            metrics.increment(
                "sentinel.flagged.total",
                intent=str(result.get("intent") or ""),
                policy_risk=str(verdict.policy_risk).lower(),
            )
    if metrics and verdict.checked:
        metrics.increment(
            "sentinel.check.total",
            outcome="flagged" if verdict.flagged else "passed",
        )

    response = await _persist_result(
        session,
        user,
        conversation,
        message,
        result,
        container,
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
    container: Container,
) -> ChatResponse:
    intent = result.get("intent")
    answer = result.get("answer") or "抱歉，我暂时无法处理您的请求。"
    sources = result.get("sources") or []
    tool_evidence = result.get("tool_evidence") or {}
    handoff_payload = result.get("handoff")
    after_sale_context = result.get("after_sale_context")
    actions = list(result.get("actions") or [])
    if (
        intent == "after_sale"
        and not handoff_payload
        and any(term in message for term in ("退货", "换货", "退换"))
    ):
        actions = [
            {
                "type": "return_request",
                "label": "申请退换货",
                "description": "上传商品照片并填写订单信息",
            }
        ]
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

    is_knowledge_gap = intent == "knowledge_gap"
    ticket_policy: dict | None = None
    handoff_out = None
    limit_exceeded = False

    if handoff_payload:
        reason = str(handoff_payload.get("reason") or "")
        threshold = tool_evidence.get("threshold")
        exception = handoff_service.is_ticket_limit_exception(message, reason)
        if isinstance(threshold, dict) and threshold.get("exceeded") is True:
            exception = True
        if "provider_failure" in tool_evidence:
            exception = True
        try:
            ticket_result = await handoff_service.create_or_reuse_ticket(
                session,
                conversation_id=conversation.id,
                user_id=user.id,
                question=message,
                summary=handoff_payload.get("summary", message),
                slots=handoff_payload.get("slots", {}),
                recent_messages=[{"role": "user", "content": message}],
                evidence=handoff_payload.get("evidence", {}),
                kind="knowledge_gap" if is_knowledge_gap else "human",
                exception=exception,
                settings=container.settings,
                embeddings=container.providers.embeddings,
            )
            ticket = ticket_result.ticket
            ticket_policy = {
                "action": "reused" if ticket_result.duplicate else "created",
                "ticket_id": ticket.id,
                "active_count": ticket_result.active_count,
                "similarity": (
                    round(ticket_result.similarity, 4)
                    if ticket_result.similarity is not None
                    else None
                ),
                "escalated": ticket_result.escalated,
            }
            if not is_knowledge_gap:
                if ticket_result.duplicate and not ticket_result.escalated:
                    answer = DUPLICATE_TICKET_REPLY
                handoff_out = {
                    "id": ticket.id,
                    "status": ticket.status,
                    "reason": reason,
                }
        except handoff_service.HandoffLimitExceeded as error:
            limit_exceeded = True
            answer = handoff_service.ticket_limit_reply(error.tickets)
            ticket_policy = {
                "action": "limit_exceeded",
                "ticket_ids": [ticket.id for ticket in error.tickets],
                "active_count": len(error.tickets),
            }
            # The graph produced a handoff payload, but no ticket was created.
            # Clear it so callers and metrics do not report a false transfer.
            result["handoff"] = None
            handoff_payload = None

    result["ticket_policy"] = ticket_policy
    trace = {
        "intent": intent,
        "slots": slots,
        "sources": sources,
        "tool_evidence": tool_evidence,
        "handoff_reason": (
            None
            if is_knowledge_gap or limit_exceeded
            else handoff_payload.get("reason") if handoff_payload else None
        ),
        "knowledge_gap": is_knowledge_gap and not limit_exceeded,
        "ticket_policy": ticket_policy,
        "sentinel": result.get("sentinel"),
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
        actions=actions,
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

    await session.commit()

    return ChatResponse(
        answer=answer,
        intent=intent,
        sources=sources,
        actions=actions,
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

    policy = result.get("ticket_policy") or {}
    action = policy.get("action")
    reason = (
        "knowledge_unavailable"
        if intent == "knowledge_gap"
        else _handoff_reason(handoff, evidence) if handoff else "other"
    )
    if action == "created":
        metrics.increment("handoff.created.total", reason=reason)
        if intent == "knowledge_gap":
            metrics.increment("knowledge_gap.created.total")
    elif action == "reused":
        metrics.increment("handoff.deduplicated.total", reason=reason)
    elif action == "limit_exceeded":
        metrics.increment("handoff.limit_exceeded.total", reason=reason)


def _tool_outcome(
    intent: str | None,
    evidence: dict,
    after_sale_context: dict | None,
) -> str:
    if isinstance(after_sale_context, dict):
        if after_sale_context.get("status") == "query_error":
            return "failure"
    if intent in ("handoff", "knowledge_gap") and evidence and "provider_failure" not in evidence:
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
