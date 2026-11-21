"""Knowledge retrieval and administration schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


@dataclass
class SourceCitation:
    title: str
    source_uri: str
    kb_type: str
    parent_id: int
    chunk_id: int
    score: float
    document_version_id: int | None
    file_name: str
    checksum: str
    page_start: int | None
    page_end: int | None
    section_path: str


@dataclass
class RetrievalResult:
    sources: list[SourceCitation] = field(default_factory=list)
    context: str = ""
    parent_ids: list[int] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.parent_ids


KnowledgeType = Literal["product", "policy"]
KnowledgeOrigin = Literal["manual", "text", "word", "pdf", "image", "unknown"]
IngestionStatus = Literal["succeeded", "failed", "unchanged"]


class KnowledgeUpsert(BaseModel):
    source_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    title: str = Field(min_length=1, max_length=300)
    kb_type: KnowledgeType
    applicability: str = Field(default="", max_length=2000)
    content: str = Field(min_length=1, max_length=500_000)
    effective_date: date | None = None
    source_kind: KnowledgeOrigin | None = None
    original_filename: str | None = Field(default=None, max_length=500)

    @field_validator("source_key", "title", "applicability", "content")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped and cls.model_fields["content"]:
            return stripped
        return stripped


class KnowledgeJobOut(BaseModel):
    id: int
    status: str
    parent_count: int
    chunk_count: int
    failure_reason: str
    started_at: datetime
    finished_at: datetime | None


class KnowledgeItemOut(BaseModel):
    id: int
    source_key: str
    title: str
    kb_type: str
    source_uri: str
    applicability: str
    effective_date: date | None
    origin: KnowledgeOrigin
    can_edit: bool
    status: str
    current_version_number: int | None
    checksum: str
    parent_count: int
    chunk_count: int
    published_at: datetime | None
    latest_job: KnowledgeJobOut | None


class KnowledgeDetailOut(KnowledgeItemOut):
    content: str


class KnowledgeIngestionOut(BaseModel):
    status: IngestionStatus
    success_count: int
    documents_seen: int
    documents_indexed: int
    documents_skipped: int
    versions_published: int
    chunks_indexed: int
    failure_reason: str = ""


class KnowledgeUpsertOut(BaseModel):
    source_key: str
    ingestion: KnowledgeIngestionOut
    item: KnowledgeItemOut | None


class KnowledgeFilePreviewOut(BaseModel):
    source_key: str
    title: str
    source_kind: KnowledgeOrigin
    original_filename: str
    content: str
    applicability: str
    effective_date: date | None
    page_count: int
    warnings: list[str]
