"""Integration tests for per-user handoff dedupe and unresolved limits."""

from __future__ import annotations

import math

import pytest

from app.db.models import Conversation, HandoffTicket
from app.services import handoff as handoff_service
from tests.helpers import create_user


class SimilarityEmbeddings:
    """Return controlled 1024-dim vectors for deterministic cosine checks."""

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self.mapping = mapping

    async def embed_query(self, text: str) -> list[float]:
        return self.mapping[text]


def _unit(cosine: float) -> list[float]:
    return [cosine, math.sqrt(max(0.0, 1.0 - cosine * cosine)), *([0.0] * 1022)]


async def _seed_ticket(
    db_session,
    *,
    user_id: int,
    conversation_id: int,
    summary: str,
    kind: str = "knowledge_gap",
    status: str = "open",
    question_embedding: list[float] | None = None,
) -> HandoffTicket:
    ticket = HandoffTicket(
        conversation_id=conversation_id,
        user_id=user_id,
        status=status,
        kind=kind,
        summary=summary,
        slots={},
        recent_messages=[],
        evidence={},
        question_norm=handoff_service.normalize_question(summary),
        question_embedding=question_embedding,
    )
    db_session.add(ticket)
    await db_session.flush()
    await db_session.refresh(ticket)
    return ticket


@pytest.mark.anyio
async def test_exact_normalized_question_reuses_ticket(db_session):
    user = await create_user(db_session, "dedupe-exact-user")
    first_conversation = Conversation(user_id=user.id, title="第一会话")
    second_conversation = Conversation(user_id=user.id, title="第二会话")
    db_session.add_all([first_conversation, second_conversation])
    await db_session.flush()

    first = await _seed_ticket(
        db_session,
        user_id=user.id,
        conversation_id=first_conversation.id,
        summary="Mate 90 的电池容量是多少？",
    )

    result = await handoff_service.create_or_reuse_ticket(
        db_session,
        conversation_id=second_conversation.id,
        user_id=user.id,
        question="Mate90电池容量",
        summary="Mate90电池容量",
        slots={},
        recent_messages=[],
        evidence={},
        kind="knowledge_gap",
    )

    assert result.created is False
    assert result.duplicate is True
    assert result.ticket.id == first.id
    assert result.ticket.conversation_id == first_conversation.id
    assert result.ticket.occurrence_count == 2
    assert result.ticket.last_seen_at is not None

    count = (
        await db_session.execute(
            HandoffTicket.__table__.select().where(HandoffTicket.user_id == user.id)
        )
    ).all()
    assert len(count) == 1


@pytest.mark.anyio
async def test_semantic_question_reuses_ticket_across_conversations(db_session):
    user = await create_user(db_session, "dedupe-semantic-user")
    first_conversation = Conversation(user_id=user.id, title="第一会话")
    second_conversation = Conversation(user_id=user.id, title="第二会话")
    db_session.add_all([first_conversation, second_conversation])
    await db_session.flush()

    first = await _seed_ticket(
        db_session,
        user_id=user.id,
        conversation_id=first_conversation.id,
        summary="怎么申请退款",
        question_embedding=_unit(1.0),
    )
    embeddings = SimilarityEmbeddings(
        {
            "退款怎么申请": _unit(0.95),
            "怎么申请退款": _unit(1.0),
        }
    )

    result = await handoff_service.create_or_reuse_ticket(
        db_session,
        conversation_id=second_conversation.id,
        user_id=user.id,
        question="退款怎么申请",
        summary="退款怎么申请",
        slots={},
        recent_messages=[],
        evidence={},
        kind="knowledge_gap",
        embeddings=embeddings,
    )

    assert result.duplicate is True
    assert result.ticket.id == first.id
    assert result.similarity == 0.95


@pytest.mark.anyio
async def test_model_number_conflict_prevents_merge(db_session):
    user = await create_user(db_session, "dedupe-model-guard-user")
    conversation = Conversation(user_id=user.id, title="型号守卫")
    db_session.add(conversation)
    await db_session.flush()

    mate_80 = await _seed_ticket(
        db_session,
        user_id=user.id,
        conversation_id=conversation.id,
        summary="Mate 80 的电池容量是多少？",
        question_embedding=_unit(0.99),
    )
    embeddings = SimilarityEmbeddings(
        {
            "Mate 90 的电池容量是多少？": _unit(1.0),
            "Mate 80 的电池容量是多少？": _unit(0.99),
        }
    )

    result = await handoff_service.create_or_reuse_ticket(
        db_session,
        conversation_id=conversation.id,
        user_id=user.id,
        question="Mate 90 的电池容量是多少？",
        summary="Mate 90 的电池容量是多少？",
        slots={},
        recent_messages=[],
        evidence={},
        kind="knowledge_gap",
        embeddings=embeddings,
    )

    assert result.created is True
    assert result.ticket.id != mate_80.id


@pytest.mark.anyio
async def test_third_distinct_unresolved_ticket_is_limited(db_session):
    user = await create_user(db_session, "limit-user")
    conversation = Conversation(user_id=user.id, title="限额")
    db_session.add(conversation)
    await db_session.flush()
    await _seed_ticket(
        db_session,
        user_id=user.id,
        conversation_id=conversation.id,
        summary="订单一直没发货",
        kind="human",
    )
    await _seed_ticket(
        db_session,
        user_id=user.id,
        conversation_id=conversation.id,
        summary="怎么申请退款",
    )

    with pytest.raises(handoff_service.HandoffLimitExceeded) as exc_info:
        await handoff_service.create_or_reuse_ticket(
            db_session,
            conversation_id=conversation.id,
            user_id=user.id,
            question="Mate 90 的电池容量是多少？",
            summary="Mate 90 的电池容量是多少？",
            slots={},
            recent_messages=[],
            evidence={},
            kind="knowledge_gap",
        )

    tickets = exc_info.value.tickets
    assert len(tickets) == 2
    reply = handoff_service.ticket_limit_reply(tickets)
    assert reply == "抱歉，您的服务需求太多了，我们先处理“订单一直没发货”和“怎么申请退款”问题吧。"


@pytest.mark.anyio
async def test_duplicate_still_reuses_when_quota_is_full(db_session):
    user = await create_user(db_session, "limit-duplicate-user")
    conversation = Conversation(user_id=user.id, title="满额重复")
    db_session.add(conversation)
    await db_session.flush()
    first = await _seed_ticket(
        db_session,
        user_id=user.id,
        conversation_id=conversation.id,
        summary="订单一直没发货",
        kind="human",
    )
    await _seed_ticket(
        db_session,
        user_id=user.id,
        conversation_id=conversation.id,
        summary="怎么申请退款",
    )

    result = await handoff_service.create_or_reuse_ticket(
        db_session,
        conversation_id=conversation.id,
        user_id=user.id,
        question="订单一直没发货",
        summary="订单一直没发货",
        slots={},
        recent_messages=[],
        evidence={},
        kind="human",
    )

    assert result.duplicate is True
    assert result.ticket.id == first.id
    assert result.ticket.occurrence_count == 2


@pytest.mark.anyio
async def test_complaint_bypasses_limit(db_session):
    user = await create_user(db_session, "limit-complaint-user")
    conversation = Conversation(user_id=user.id, title="投诉例外")
    db_session.add(conversation)
    await db_session.flush()
    await _seed_ticket(
        db_session,
        user_id=user.id,
        conversation_id=conversation.id,
        summary="订单一直没发货",
        kind="human",
    )
    await _seed_ticket(
        db_session,
        user_id=user.id,
        conversation_id=conversation.id,
        summary="怎么申请退款",
    )

    result = await handoff_service.create_or_reuse_ticket(
        db_session,
        conversation_id=conversation.id,
        user_id=user.id,
        question="我要投诉你们",
        summary="我要投诉你们",
        slots={},
        recent_messages=[],
        evidence={},
        kind="human",
        exception=True,
    )

    assert result.created is True
    assert result.ticket.kind == "human"


@pytest.mark.anyio
async def test_knowledge_gap_escalates_to_human_without_second_ticket(db_session):
    user = await create_user(db_session, "escalation-user")
    conversation = Conversation(user_id=user.id, title="升级")
    db_session.add(conversation)
    await db_session.flush()
    gap = await _seed_ticket(
        db_session,
        user_id=user.id,
        conversation_id=conversation.id,
        summary="Mate 90 的电池容量是多少？",
        kind="knowledge_gap",
    )

    result = await handoff_service.create_or_reuse_ticket(
        db_session,
        conversation_id=conversation.id,
        user_id=user.id,
        question="Mate 90 的电池容量是多少？",
        summary="Mate 90 的电池容量是多少？",
        slots={},
        recent_messages=[],
        evidence={},
        kind="human",
    )

    assert result.duplicate is True
    assert result.escalated is True
    assert result.ticket.id == gap.id
    assert result.ticket.kind == "human"
    assert result.ticket.status == "open"
