"""Shared state values used across storage, APIs, logs, and provider code."""

from __future__ import annotations

from enum import StrEnum


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Intent(StrEnum):
    GREETING = "greeting"
    PRODUCT_KNOWLEDGE = "product_knowledge"
    POLICY_KNOWLEDGE = "policy_knowledge"
    PRICE_STOCK = "price_stock"
    ORDER = "order"
    AFTER_SALE = "after_sale"
    CHIT_CHAT = "chit_chat"
    CLARIFICATION = "clarification"
    HANDOFF = "handoff"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ToolName(StrEnum):
    ORDER = "order"
    AFTER_SALE = "after_sale"
    CATALOG = "catalog"
    KNOWLEDGE = "knowledge"
    NONE = "none"


class AfterSaleContextStatus(StrEnum):
    HAS_RECORDS = "has_records"
    NO_RECORDS = "no_records"
    NOT_AVAILABLE = "not_available"
    QUERY_ERROR = "query_error"


class HandoffStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class ProviderStatus(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"

