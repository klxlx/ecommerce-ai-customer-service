"""Rebuild knowledge embeddings without deleting documents.

Two operations are supported:

1. Re-embed the chunks of every published active knowledge document in place.
   This is used after switching ``EMBEDDING_MODEL`` (for example from
   ``text-embedding-v3`` to ``text-embedding-v4``). Content, source keys,
   versions and visibility are preserved; only vectors and the version's
   embedding-model label change.
2. Retry active PDF documents that have no current version (failed ingestion)
   by extracting their original PDF and publishing a new version.

Usage:
    python -m scripts.rebuild_knowledge
    python -m scripts.rebuild_knowledge --skip-handoffs
    python -m scripts.rebuild_knowledge --skip-failed
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from datetime import date
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from pypdf import PdfReader
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import (
    EMBEDDING_DIMENSION,
    HandoffTicket,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
)
from app.db.session import create_session_factory
from app.rag.embeddings import DashScopeEmbeddings
from app.rag.indexer import index_extracted_documents
from app.rag.pdf_extraction import PdfSource, extract_pdf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PDF_ROOT = PROJECT_ROOT / "data" / "knowledge" / "pdf_sources"
CHUNK_BATCH = 10
ACTIVE_HANDOFF_STATUSES = {"open", "in_progress"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_pdf(filename: str) -> Path | None:
    if not filename:
        return None
    direct = PDF_ROOT / filename
    if direct.is_file():
        return direct
    for candidate in PROJECT_ROOT.rglob(filename):
        if candidate.is_file():
            return candidate
    return None


def _extraction_strategy(document: KnowledgeDocument, version) -> str:
    if version is not None and version.extraction_strategy in {
        "huawei_outline",
        "jd_clauses",
    }:
        return version.extraction_strategy
    name = f"{document.title} {document.original_filename}".upper()
    if "HUAWEI" in name or "华为" in name:
        return "huawei_outline"
    return "jd_clauses"


def _effective_date(document: KnowledgeDocument) -> str | None:
    if isinstance(document.effective_date, date):
        return document.effective_date.isoformat()
    return None


async def _reembed_published(
    session,
    provider: DashScopeEmbeddings,
    target_model: str,
) -> tuple[int, int]:
    versions = (
        await session.execute(
            select(KnowledgeDocument, KnowledgeDocumentVersion)
            .join(
                KnowledgeDocumentVersion,
                KnowledgeDocument.current_version_id == KnowledgeDocumentVersion.id,
            )
            .where(
                KnowledgeDocument.deleted_at.is_(None),
                KnowledgeDocumentVersion.status == "published",
                KnowledgeDocumentVersion.embedding_model != target_model,
            )
            .order_by(KnowledgeDocument.id)
        )
    ).all()
    total_chunks = 0
    for document, version in versions:
        chunks = (
            await session.execute(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_version_id == version.id)
                .order_by(KnowledgeChunk.id)
            )
        ).scalars().all()
        if not chunks:
            print(f"SKIP document={document.id} version={version.id} reason=no_chunks")
            continue
        print(
            f"REEMBED document={document.id} version={version.id} "
            f"chunks={len(chunks)} title={document.title}"
        )
        try:
            for offset in range(0, len(chunks), CHUNK_BATCH):
                batch = chunks[offset : offset + CHUNK_BATCH]
                vectors = await provider.embed_documents(
                    [chunk.content for chunk in batch]
                )
                if len(vectors) != len(batch):
                    raise ValueError("embedding count mismatch")
                for chunk, vector in zip(batch, vectors, strict=True):
                    if len(vector) != EMBEDDING_DIMENSION:
                        raise ValueError("embedding dimension mismatch")
                    chunk.embedding = vector
                await session.flush()
            version.embedding_model = target_model
            version.failure_reason = ""
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        total_chunks += len(chunks)
        print(f"  done document={document.id} chunks={len(chunks)}")
    return len(versions), total_chunks


async def _retry_failed(
    session,
    provider: DashScopeEmbeddings,
    target_model: str,
) -> int:
    documents = (
        await session.execute(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.deleted_at.is_(None),
                KnowledgeDocument.current_version_id.is_(None),
            )
            .order_by(KnowledgeDocument.id)
        )
    ).scalars().all()
    published = 0
    for document in documents:
        filename = document.original_filename or document.source_uri
        path = _resolve_pdf(filename.removeprefix("manual://"))
        if path is None:
            print(
                f"SKIP document={document.id} reason=source_file_missing "
                f"filename={filename}"
            )
            continue
        version = (
            await session.execute(
                select(KnowledgeDocumentVersion)
                .where(
                    KnowledgeDocumentVersion.document_id == document.id,
                    KnowledgeDocumentVersion.embedding_model == target_model,
                )
                .order_by(KnowledgeDocumentVersion.id.desc())
            )
        ).scalars().first()
        source = PdfSource(
            source_id=document.source_key,
            file_name=path.name,
            title=document.title,
            kb_type=document.kb_type,
            applicability=document.applicability,
            extraction_strategy=_extraction_strategy(document, version),
            page_count=len(PdfReader(str(path)).pages),
            effective_date=_effective_date(document),
            checksum=_sha256(path),
        )
        print(
            f"REINGEST document={document.id} file={path.name} "
            f"strategy={source.extraction_strategy}"
        )
        try:
            extracted = extract_pdf(source, path.parent)
            result = await index_extracted_documents(
                session,
                provider,
                [extracted],
                embedding_model=target_model,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            print(f"  failed document={document.id}", file=sys.stderr)
            raise
        for error in result.errors:
            print(f"  ERROR {error}", file=sys.stderr)
        if result.documents_indexed:
            published += 1
            print(
                f"  published document={document.id} "
                f"chunks={result.chunks_indexed}"
            )
        else:
            print(f"  skipped document={document.id}")
    return published


async def _reembed_handoffs(
    session,
    provider: DashScopeEmbeddings,
) -> int:
    tickets = (
        await session.execute(
            select(HandoffTicket)
            .where(
                HandoffTicket.status.in_(ACTIVE_HANDOFF_STATUSES),
                HandoffTicket.question_embedding.is_not(None),
            )
            .order_by(HandoffTicket.id)
        )
    ).scalars().all()
    if not tickets:
        return 0
    texts = [ticket.summary or ticket.question_norm for ticket in tickets]
    vectors = await provider.embed_documents(texts)
    if len(vectors) != len(tickets):
        raise ValueError("handoff embedding count mismatch")
    for ticket, vector in zip(tickets, vectors, strict=True):
        if len(vector) != EMBEDDING_DIMENSION:
            raise ValueError("handoff embedding dimension mismatch")
        ticket.question_embedding = vector
    await session.commit()
    return len(tickets)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-reembed", action="store_true")
    parser.add_argument("--skip-failed", action="store_true")
    parser.add_argument("--skip-handoffs", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    target_model = settings.embedding_model
    provider = DashScopeEmbeddings(settings)
    factory = create_session_factory(settings.database_url)
    print(f"target_embedding_model={target_model}")

    async with factory() as session:
        if not args.skip_reembed:
            versions, chunks = await _reembed_published(
                session, provider, target_model
            )
            print(f"reembedded_versions={versions} chunks={chunks}")
        if not args.skip_failed:
            published = await _retry_failed(session, provider, target_model)
            print(f"reingested_documents={published}")
        if not args.skip_handoffs:
            tickets = await _reembed_handoffs(session, provider)
            print(f"reembedded_handoff_tickets={tickets}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
