"""SQLAlchemy ORM models for the e-commerce customer service system.

Dynamic business facts (product price/stock, order, item, and logistics data)
live only in the relational tables here and are never written to vector columns.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base, TimestampMixin

EMBEDDING_DIMENSION = 1024


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # customer | admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="sessions")


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="新对话", nullable=False)
    slots: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    chit_chat_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clarification_attempts: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Optional customer-side actions (e.g. the return/exchange card).
    actions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    after_sale_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    trace_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint(
            "source_key",
            name="uq_knowledge_documents_source_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    kb_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    applicability: Mapped[str] = mapped_column(Text, default="", nullable=False)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "knowledge_document_versions.id",
            name="fk_knowledge_documents_current_version_id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    source_kind: Mapped[str] = mapped_column(
        String(16), default="unknown", server_default="unknown", nullable=False
    )
    original_filename: Mapped[str] = mapped_column(
        String(500), default="", server_default="", nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    versions: Mapped[list["KnowledgeDocumentVersion"]] = relationship(
        back_populates="document",
        foreign_keys="KnowledgeDocumentVersion.document_id",
        cascade="all, delete-orphan",
    )
    current_version: Mapped["KnowledgeDocumentVersion | None"] = relationship(
        foreign_keys=[current_version_id],
        post_update=True,
    )


class KnowledgeDocumentVersion(Base, TimestampMixin):
    __tablename__ = "knowledge_document_versions"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_knowledge_document_versions_document_number",
        ),
        UniqueConstraint(
            "document_id",
            "checksum",
            "chunk_size",
            "overlap",
            "embedding_model",
            name="uq_knowledge_document_versions_config",
        ),
        Index(
            "uq_knowledge_document_versions_one_published",
            "document_id",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    overlap: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="draft", nullable=False, index=True
    )
    pdf_file: Mapped[str] = mapped_column(String(500), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    extraction_strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    document: Mapped["KnowledgeDocument"] = relationship(
        back_populates="versions",
        foreign_keys=[document_id],
    )
    parents: Mapped[list["KnowledgeParent"]] = relationship(
        back_populates="document_version"
    )
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="document_version"
    )


class KnowledgeIngestionJob(Base):
    __tablename__ = "knowledge_ingestion_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_document_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(16), default="running", nullable=False, index=True
    )
    parent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )

    document: Mapped["KnowledgeDocument"] = relationship()
    document_version: Mapped["KnowledgeDocumentVersion | None"] = relationship()


class KnowledgeSource(Base, TimestampMixin):
    __tablename__ = "knowledge_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_uri: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    kb_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    parents: Mapped[list["KnowledgeParent"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class KnowledgeParent(Base, TimestampMixin):
    __tablename__ = "knowledge_parents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    document_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_document_versions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[str] = mapped_column(Text, default="", nullable=False)

    source: Mapped["KnowledgeSource"] = relationship(back_populates="parents")
    document_version: Mapped["KnowledgeDocumentVersion | None"] = relationship(
        back_populates="parents"
    )
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )


class KnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_parents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSION), nullable=False
    )
    document_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_document_versions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[str] = mapped_column(Text, default="", nullable=False)

    parent: Mapped["KnowledgeParent"] = relationship(back_populates="chunks")
    document_version: Mapped["KnowledgeDocumentVersion | None"] = relationship(
        back_populates="chunks"
    )


class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    model: Mapped[str] = mapped_column(
        String(120), default="", server_default="", nullable=False
    )
    color: Mapped[str] = mapped_column(
        String(80), default="", server_default="", nullable=False
    )
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False)


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    logistics: Mapped[list["LogisticsEvent"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    model: Mapped[str] = mapped_column(
        String(120), default="", server_default="", nullable=False
    )
    color: Mapped[str] = mapped_column(
        String(80), default="", server_default="", nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order: Mapped["Order"] = relationship(back_populates="items")


class LogisticsEvent(Base):
    __tablename__ = "logistics_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    location: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    order: Mapped["Order"] = relationship(back_populates="logistics")


class AfterSale(Base, TimestampMixin):
    __tablename__ = "after_sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trace: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )

    tool_calls: Mapped[list["ToolCallLog"]] = relationship(
        back_populates="agent_run", cascade="all, delete-orphan"
    )


class ToolCallLog(Base):
    __tablename__ = "tool_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )

    agent_run: Mapped["AgentRun"] = relationship(back_populates="tool_calls")


class HandoffTicket(Base, TimestampMixin):
    __tablename__ = "handoff_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    # human | knowledge_gap：知识缺口工单不进入客服回复，也不阻断智能体。
    kind: Mapped[str] = mapped_column(
        String(24), default="human", server_default="human", nullable=False
    )
    # general | return_exchange: return tickets bypass the unresolved cap.
    category: Mapped[str] = mapped_column(
        String(32), default="general", server_default="general", nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Normalized question + embedding support cross-conversation dedupe.
    question_norm: Mapped[str] = mapped_column(
        String(512), default="", server_default="", nullable=False
    )
    question_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSION), nullable=True
    )
    occurrence_count: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    slots: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    recent_messages: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Human-agent console: a ticket is claimed by exactly one administrator.
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # returned_to_agent | resolved_by_agent | timeout
    close_reason: Mapped[str] = mapped_column(
        String(24), default="", server_default="", nullable=False
    )
    resolution_note: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )


class ReturnAnalysis(Base, TimestampMixin):
    """Server-owned photo analysis result; the client only references its id."""

    __tablename__ = "return_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    image_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    image_path: Mapped[str] = mapped_column(String(500), nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failure_reason: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SemanticCache(Base, TimestampMixin):
    """Curated FAQ cache; entries are imported, never auto-generated."""

    __tablename__ = "semantic_cache"
    __table_args__ = (
        UniqueConstraint(
            "question_norm", "kb_type", name="uq_semantic_cache_question_kb"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_norm: Mapped[str] = mapped_column(
        String(512), nullable=False, index=True
    )
    kb_type: Mapped[str] = mapped_column(
        String(32), default="policy", server_default="policy", nullable=False
    )
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSION), nullable=False
    )
    hit_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# Vector index for child-chunk similarity search.
Index(
    "ix_knowledge_chunks_embedding",
    KnowledgeChunk.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
)

# Fast unresolved-ticket lookup for per-user dedupe and ticket limits.
Index(
    "ix_handoff_tickets_user_status",
    HandoffTicket.user_id,
    HandoffTicket.status,
)

Index(
    "ix_semantic_cache_embedding",
    SemanticCache.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
)
