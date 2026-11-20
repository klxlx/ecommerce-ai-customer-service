"""Handoff service: queue, live console, claim, reply, and release."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import ChatMessage, HandoffTicket, User
from app.db.repositories import auth as auth_repo
from app.db.repositories import chat as chat_repo
from app.db.repositories import handoff as handoff_repo
from app.graph.router import is_complaint_or_urgent


class HandoffError(Exception):
    """Base class for handoff state errors mapped to HTTP responses."""


class HandoffNotFound(HandoffError):
    """The ticket does not exist."""


class HandoffConflict(HandoffError):
    """The ticket has already been claimed by another administrator."""

    def __init__(self, assignee_id: int | None, assignee_name: str = "") -> None:
        super().__init__("handoff already claimed")
        self.assignee_id = assignee_id
        self.assignee_name = assignee_name


class HandoffForbidden(HandoffError):
    """The caller is not the administrator who owns this ticket."""


class HandoffLimitExceeded(HandoffError):
    """The user already has the maximum number of unresolved tickets."""

    def __init__(self, tickets: list[HandoffTicket]) -> None:
        super().__init__("handoff limit exceeded")
        self.tickets = tickets


@dataclass
class TicketCreationResult:
    """Outcome of creating or reusing a handoff ticket."""

    ticket: HandoffTicket
    created: bool
    duplicate: bool
    escalated: bool
    similarity: float | None
    active_count: int


MAX_UNRESOLVED_TICKETS = 2
DEFAULT_DEDUPE_SIMILARITY = 0.93
MAX_SUMMARY_CHARS = 26

_QUESTION_PREFIX = re.compile(
    r"^(?:请问|想问一下|想问|我想问|我想咨询|咨询一下|帮我|请帮我|麻烦|劳驾)\s*"
)
_HANDOFF_CANONICAL = re.compile(
    r"^(?:我要|我想|请|麻烦|帮我|找|联系)?"
    r"(?:转人工|人工客服|人工服务|人工)\s*$"
)
_MODEL_TOKEN = re.compile(r"([a-z]{2,}\d{2,}[a-z0-9]*)")
_ORDER_TOKEN = re.compile(r"([a-z]{2,}[-_]?\d{4,})")
_SERVICE_TERMS = ("退货退款", "退款", "退货", "换货", "售后")
_PROPERTY_TERMS = (
    "电池容量",
    "续航",
    "屏幕尺寸",
    "屏幕",
    "相机",
    "拍照",
    "内存",
    "存储",
    "重量",
    "尺寸",
    "充电",
    "发热",
    "保修",
    "价格",
    "库存",
)
_PROPERTY_SYNONYMS = {
    "发烫": "发热",
    "电池多大": "电池容量",
    "电量": "电池容量",
}
_NEGATION = re.compile(r"(?:不|没|未|无|不能|无法|不可)")
_COMPLAINT_ACTION = re.compile(
    r"(我要投诉|投诉你们|已经投诉|投诉过|举报|12315|消协|工商|起诉|报警|欺诈|欺骗|骗子)"
)
_URGENT = re.compile(r"(紧急|加急|急事|马上处理|立刻处理|人身安全|安全问题)")


def normalize_question(text: str) -> str:
    """Normalize a customer question for exact and semantic comparison."""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    normalized = re.sub(r"\s+", "", normalized)
    normalized = _QUESTION_PREFIX.sub("", normalized)
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)
    normalized = normalized.strip("_").replace("的", "")
    normalized = re.sub(
        r"(?:是多少|多少|是什么|怎么样|怎么办|怎么弄|吗|呢|呀|啊|吧)$",
        "",
        normalized,
    )
    if _HANDOFF_CANONICAL.match(normalized):
        return "转人工"
    return normalized


def ticket_limit_reply(tickets: list[HandoffTicket]) -> str:
    """Build the customer-facing message when the unresolved limit is hit."""
    summaries: list[str] = []
    for ticket in tickets[:MAX_UNRESOLVED_TICKETS]:
        summary = " ".join((ticket.summary or "").split()).strip()
        if not summary:
            summary = "已有问题"
        if len(summary) > MAX_SUMMARY_CHARS:
            summary = f"{summary[:MAX_SUMMARY_CHARS].rstrip('，,。；;、')}…"
        summaries.append(summary)
    quoted = "和".join(f"“{summary}”" for summary in summaries)
    return f"抱歉，您的服务需求太多了，我们先处理{quoted}问题吧。"


def _cosine(left: list[float] | None, right: list[float] | None) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    dot = math.fsum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(math.fsum(a * a for a in left))
    norm_right = math.sqrt(math.fsum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return None
    return dot / (norm_left * norm_right)


def _models(text: str) -> set[str]:
    return set(_MODEL_TOKEN.findall(normalize_question(text)))


def _orders(text: str) -> set[str]:
    return set(_ORDER_TOKEN.findall(normalize_question(text)))


def _service_terms(text: str) -> set[str]:
    raw = text or ""
    found = {term for term in _SERVICE_TERMS if term in raw}
    return found


def _property_terms(text: str) -> set[str]:
    raw = text or ""
    for source, target in _PROPERTY_SYNONYMS.items():
        if source in raw:
            found = {target}
            for term in _PROPERTY_TERMS:
                if term in raw:
                    found.add(term)
            return found & set(_PROPERTY_TERMS)
    return {term for term in _PROPERTY_TERMS if term in raw}


def _has_negation(text: str) -> bool:
    return bool(_NEGATION.search(text or ""))


def _same_question(
    left_text: str,
    right_text: str,
    left_embedding: list[float] | None,
    right_embedding: list[float] | None,
    *,
    threshold: float,
) -> tuple[bool, float | None]:
    left_norm = normalize_question(left_text)
    right_norm = normalize_question(right_text)
    if not left_norm or not right_norm:
        return False, None
    if left_norm == right_norm:
        return True, 1.0

    left_models = _models(left_text)
    right_models = _models(right_text)
    if left_models and right_models and left_models != right_models:
        return False, None

    left_orders = _orders(left_text)
    right_orders = _orders(right_text)
    if left_orders and right_orders and left_orders != right_orders:
        return False, None

    left_service = _service_terms(left_text)
    right_service = _service_terms(right_text)
    if left_service and right_service and left_service != right_service:
        return False, None

    left_property = _property_terms(left_text)
    right_property = _property_terms(right_text)
    if left_property and right_property and left_property != right_property:
        return False, None

    if _has_negation(left_text) != _has_negation(right_text):
        return False, None

    similarity = _cosine(left_embedding, right_embedding)
    if similarity is None:
        return False, None
    return similarity >= threshold, similarity


async def _embed(embeddings: Any, text: str) -> list[float] | None:
    if embeddings is None or not text:
        return None
    try:
        return await embeddings.embed_query(text)
    except Exception:
        # Dedupe must never block ticket creation when the embedding provider
        # is unavailable. Exact normalized matching still applies.
        return None


def is_ticket_limit_exception(message: str, reason: str) -> bool:
    """Complaints, urgent cases, and system failures bypass the unresolved cap."""
    if is_complaint_or_urgent(message):
        return True
    return reason in {
        "provider_failure",
        "threshold_exceeded",
        "tool_failure",
        "工具查询失败",
        "多次澄清失败",
    }


async def create_or_reuse_ticket(
    session: AsyncSession,
    *,
    conversation_id: int,
    user_id: int,
    question: str,
    summary: str,
    slots: dict,
    recent_messages: list,
    evidence: dict,
    kind: str = "human",
    category: str = "general",
    exception: bool = False,
    settings: Settings | None = None,
    embeddings: Any = None,
) -> TicketCreationResult:
    """Enforce per-user question dedupe and the unresolved-ticket limit."""
    threshold = (
        settings.handoff_dedupe_similarity
        if settings is not None
        else DEFAULT_DEDUPE_SIMILARITY
    )
    max_unresolved = (
        settings.handoff_max_unresolved
        if settings is not None
        else MAX_UNRESOLVED_TICKETS
    )
    now = datetime.now(timezone.utc)
    question_norm = normalize_question(question)
    query_embedding = await _embed(embeddings, question)
    # Serialize ticket decisions per user so two concurrent turns cannot both
    # pass the duplicate check and create the same question twice.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(CAST(:key AS bigint))"),
        {"key": user_id},
    )
    active = await handoff_repo.list_unresolved_for_user(session, user_id)

    duplicate: HandoffTicket | None = None
    duplicate_similarity: float | None = None
    for ticket in active:
        if ticket.category == "return_exchange":
            # Return tickets have an exact business key; never merge them into
            # a generic question by semantic similarity.
            continue
        ticket_text = ticket.summary or ""
        ticket_embedding = ticket.question_embedding
        if ticket_embedding is None:
            ticket_embedding = await _embed(embeddings, ticket_text)
            if ticket_embedding is not None:
                ticket.question_embedding = ticket_embedding
        same, similarity = _same_question(
            question,
            ticket_text,
            query_embedding,
            ticket_embedding,
            threshold=threshold,
        )
        if same:
            duplicate = ticket
            duplicate_similarity = similarity
            break

    if duplicate is not None:
        duplicate.occurrence_count = (duplicate.occurrence_count or 1) + 1
        duplicate.last_seen_at = now
        if duplicate.question_norm == "":
            duplicate.question_norm = question_norm
        if duplicate.question_embedding is None and query_embedding is not None:
            duplicate.question_embedding = query_embedding
        escalated = kind == "human" and duplicate.kind == "knowledge_gap"
        if escalated:
            # An explicit human request on an existing knowledge gap escalates
            # the same ticket instead of creating a second one.
            duplicate.kind = "human"
            duplicate.status = "open"
            duplicate.summary = summary or duplicate.summary
            duplicate.slots = slots or duplicate.slots
            duplicate.evidence = evidence or duplicate.evidence
            duplicate.resolved_at = None
            duplicate.close_reason = ""
            duplicate.resolution_note = ""
        await session.flush()
        return TicketCreationResult(
            ticket=duplicate,
            created=False,
            duplicate=True,
            escalated=escalated,
            similarity=duplicate_similarity,
            active_count=len(active),
        )

    countable = [
        ticket
        for ticket in active
        if ticket.category != "return_exchange"
    ]
    if len(countable) >= max_unresolved and not exception:
        raise HandoffLimitExceeded(countable[:max_unresolved])

    ticket = await handoff_repo.create_handoff(
        session,
        conversation_id=conversation_id,
        user_id=user_id,
        summary=summary,
        slots=slots,
        recent_messages=recent_messages,
        evidence=evidence,
        kind=kind,
        category=category,
        question_norm=question_norm,
        question_embedding=query_embedding,
        occurrence_count=1,
        last_seen_at=now,
    )
    return TicketCreationResult(
        ticket=ticket,
        created=True,
        duplicate=False,
        escalated=False,
        similarity=None,
        active_count=len(active) + 1,
    )


async def create_return_ticket(
    session: AsyncSession,
    *,
    conversation_id: int,
    user_id: int,
    summary: str,
    slots: dict,
    recent_messages: list,
    evidence: dict,
    dedupe_key: str,
) -> TicketCreationResult:
    """Create a return/exchange human ticket without the unresolved cap.

    Exact business dedupe is used instead of semantic question matching so
    different orders or damage reasons are never merged accidentally.
    """
    now = datetime.now(timezone.utc)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(CAST(:key AS bigint))"),
        {"key": user_id},
    )
    active = await handoff_repo.list_unresolved_for_user(session, user_id)
    for ticket in active:
        return_request = (ticket.evidence or {}).get("return_request") or {}
        if (
            ticket.category == "return_exchange"
            and return_request.get("dedupe_key") == dedupe_key
        ):
            ticket.occurrence_count = (ticket.occurrence_count or 1) + 1
            ticket.last_seen_at = now
            await session.flush()
            return TicketCreationResult(
                ticket=ticket,
                created=False,
                duplicate=True,
                escalated=False,
                similarity=1.0,
                active_count=len(active),
            )

    ticket = await handoff_repo.create_handoff(
        session,
        conversation_id=conversation_id,
        user_id=user_id,
        summary=summary,
        slots=slots,
        recent_messages=recent_messages,
        evidence=evidence,
        kind="human",
        category="return_exchange",
        question_norm=dedupe_key[:512],
        occurrence_count=1,
        last_seen_at=now,
    )
    return TicketCreationResult(
        ticket=ticket,
        created=True,
        duplicate=False,
        escalated=False,
        similarity=None,
        active_count=len(active) + 1,
    )


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _elapsed_seconds(value: datetime | None, now: datetime) -> int:
    moment = _utc(value)
    if moment is None:
        return 0
    return max(0, int((now - moment).total_seconds()))


async def create_ticket(
    session: AsyncSession,
    conversation_id: int,
    user_id: int,
    summary: str,
    slots: dict,
    recent_messages: list,
    evidence: dict,
    kind: str = "human",
    category: str = "general",
) -> HandoffTicket:
    return await handoff_repo.create_handoff(
        session,
        conversation_id=conversation_id,
        user_id=user_id,
        summary=summary,
        slots=slots,
        recent_messages=recent_messages,
        evidence=evidence,
        kind=kind,
        category=category,
    )


async def list_tickets(
    session: AsyncSession, status: str | None = None
) -> list[HandoffTicket]:
    return await handoff_repo.list_handoffs(session, status)


async def get_ticket(
    session: AsyncSession, handoff_id: int
) -> HandoffTicket | None:
    return await handoff_repo.get_handoff(session, handoff_id)


async def get_active_ticket(
    session: AsyncSession, conversation_id: int
) -> HandoffTicket | None:
    return await handoff_repo.get_active_for_conversation(
        session, conversation_id
    )


async def live_sessions(session: AsyncSession) -> list[dict]:
    """Serialize the active queue for the human-agent console."""
    tickets = await handoff_repo.list_active(session)
    if not tickets:
        return []

    user_ids = {ticket.user_id for ticket in tickets}
    user_ids.update(
        ticket.assignee_id for ticket in tickets if ticket.assignee_id
    )
    users = await auth_repo.get_users_by_ids(session, sorted(user_ids))
    conversation_ids = [ticket.conversation_id for ticket in tickets]
    latest = await handoff_repo.latest_messages(session, conversation_ids)
    counts = await handoff_repo.message_counts(session, conversation_ids)

    now = datetime.now(timezone.utc)
    items: list[dict] = []
    for ticket in tickets:
        customer = users.get(ticket.user_id)
        assignee = users.get(ticket.assignee_id) if ticket.assignee_id else None
        last = latest.get(ticket.conversation_id)
        items.append(
            {
                "id": ticket.id,
                "conversation_id": ticket.conversation_id,
                "user_id": ticket.user_id,
                "username": customer.username if customer else "",
                "status": ticket.status,
                "kind": ticket.kind,
                "category": ticket.category,
                "assignee_id": ticket.assignee_id,
                "assignee_name": assignee.username if assignee else "",
                "summary": ticket.summary,
                "created_at": ticket.created_at,
                "claimed_at": ticket.claimed_at,
                "waiting_seconds": _elapsed_seconds(ticket.created_at, now),
                "last_message": last.content if last else "",
                "last_message_role": last.role if last else "",
                "last_message_at": last.created_at if last else None,
                "message_count": counts.get(ticket.conversation_id, 0),
            }
        )
    return items


async def claim(
    session: AsyncSession, handoff_id: int, admin: User
) -> HandoffTicket:
    """Claim a waiting ticket for ``admin`` or raise a typed error."""
    claimed = await handoff_repo.claim(session, handoff_id, admin.id)
    if not claimed:
        ticket = await handoff_repo.get_handoff(session, handoff_id)
        if ticket is None:
            raise HandoffNotFound
        assignee = (
            await auth_repo.get_user_by_id(session, ticket.assignee_id)
            if ticket.assignee_id
            else None
        )
        raise HandoffConflict(
            ticket.assignee_id,
            assignee.username if assignee else "",
        )

    ticket = await handoff_repo.get_handoff(session, handoff_id)
    if ticket is None:  # pragma: no cover - guarded by the update above
        raise HandoffNotFound
    await chat_repo.add_message(
        session,
        ticket.conversation_id,
        "system",
        "人工客服已接入，请稍候。",
    )
    await session.refresh(ticket)
    return ticket


def _require_owner(ticket: HandoffTicket | None, admin: User) -> HandoffTicket:
    if ticket is None:
        raise HandoffNotFound
    if ticket.status != "in_progress" or ticket.assignee_id != admin.id:
        raise HandoffForbidden
    return ticket


async def reply(
    session: AsyncSession, handoff_id: int, admin: User, content: str
) -> ChatMessage:
    """Append a human-agent reply without ending the session.

    Replying to a still-waiting ticket implicitly claims it first, so the
    queue detail page and the live console share one consistent rule: only the
    administrator who owns the ticket can write to the customer.
    """
    ticket = await handoff_repo.get_handoff(session, handoff_id)
    if ticket is not None and (
        ticket.status == "open"
        or (ticket.status == "in_progress" and ticket.assignee_id is None)
    ):
        ticket = await claim(session, handoff_id, admin)
    ticket = _require_owner(ticket, admin)
    message = await chat_repo.add_message(
        session,
        ticket.conversation_id,
        "admin",
        content,
    )
    await session.refresh(ticket)
    return message


async def release(
    session: AsyncSession,
    handoff_id: int,
    admin: User,
    note: str,
) -> HandoffTicket:
    """Close the session and hand the conversation back to the agent."""
    ticket = _require_owner(
        await handoff_repo.get_handoff(session, handoff_id), admin
    )
    now = datetime.now(timezone.utc)
    ticket.status = "resolved"
    ticket.resolved_at = now
    ticket.close_reason = "returned_to_agent"
    ticket.resolution_note = note

    await chat_repo.add_message(
        session,
        ticket.conversation_id,
        "system",
        "人工服务已结束，智能体继续为您服务。",
    )

    conversation = await chat_repo.get_conversation(
        session, ticket.conversation_id
    )
    if conversation is not None:
        slots = dict(conversation.slots or {})
        slots["last_handoff"] = {
            "ticket_id": ticket.id,
            "note": note,
            "resolved_at": now.isoformat(),
        }
        conversation.slots = slots

    await session.flush()
    await session.refresh(ticket)
    return ticket


async def update_status(
    session: AsyncSession, handoff_id: int, status: str
) -> HandoffTicket | None:
    ticket = await handoff_repo.get_handoff(session, handoff_id)
    if ticket is None:
        return None

    if status == "resolved" and ticket.status != "resolved":
        # The queue may close a ticket directly; keep the customer-facing
        # handback consistent with the live console release flow. Knowledge-gap
        # tickets never entered human service, so they must stay silent.
        ticket.resolved_at = datetime.now(timezone.utc)
        ticket.close_reason = ticket.close_reason or "marked_resolved"
        if ticket.kind == "human":
            await chat_repo.add_message(
                session,
                ticket.conversation_id,
                "system",
                "人工服务已结束，智能体继续为您服务。",
            )

    return await handoff_repo.update_status(session, ticket, status)


async def list_messages(
    session: AsyncSession,
    handoff_id: int,
    after_id: int | None = None,
) -> list[ChatMessage] | None:
    ticket = await handoff_repo.get_handoff(session, handoff_id)
    if ticket is None:
        return None
    return await chat_repo.list_messages(
        session, ticket.conversation_id, after_id=after_id
    )


async def reply_and_return_to_agent(
    session: AsyncSession, handoff_id: int, content: str
) -> tuple[ChatMessage, HandoffTicket] | None:
    """Legacy queue endpoint: reply once and close the ticket.

    Kept for the audit queue; the live console uses :func:`reply` followed by
    :func:`release` so a session can span many messages.
    """
    ticket = await handoff_repo.get_handoff(session, handoff_id)
    if ticket is None:
        return None
    message = await chat_repo.add_message(
        session,
        ticket.conversation_id,
        "admin",
        content,
    )
    await handoff_repo.update_status(session, ticket, "resolved")
    return message, ticket
