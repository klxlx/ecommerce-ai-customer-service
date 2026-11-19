"""Parent-child retrieval: child recall -> rerank -> parent expansion -> dedupe."""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    KnowledgeDocumentVersion,
    KnowledgeParent,
    KnowledgeSource,
)
from app.db.repositories import rag as rag_repo
from app.rag.embeddings import EmbeddingProvider
from app.rag.rerank import RerankProvider
from app.schemas.knowledge import RetrievalResult, SourceCitation

MAX_CONTEXT_CHARS = 12_000


@dataclass
class Providers:
    embeddings: EmbeddingProvider
    rerank: RerankProvider


@dataclass
class ChunkCandidate:
    chunk_id: int
    parent_id: int
    content: str
    score: float


def expand_parents(
    candidates: list[ChunkCandidate],
    parents: dict[int, KnowledgeParent],
    sources: dict[int, KnowledgeSource],
    versions: dict[int, KnowledgeDocumentVersion],
    max_context_chars: int = MAX_CONTEXT_CHARS,
) -> RetrievalResult:
    """Deduplicate ranked children into unique parents and format citations.

    Child content never becomes the final answer context; only full parent text
    is returned. The first (best-ranked) child determines each parent's order.
    """
    seen: set[int] = set()
    ordered: list[tuple[KnowledgeParent, ChunkCandidate]] = []
    for candidate in candidates:
        if candidate.parent_id in seen:
            continue
        parent = parents.get(candidate.parent_id)
        if parent is None:
            continue
        seen.add(candidate.parent_id)
        ordered.append((parent, candidate))

    if max_context_chars <= 0:
        raise ValueError("max_context_chars must be positive")

    citations: list[SourceCitation] = []
    blocks: list[str] = []
    context_chars = 0
    for parent, child in ordered:
        source = sources.get(parent.source_id)
        title = source.name if source else "未知来源"
        uri = source.source_uri if source else ""
        kb_type = source.kb_type if source else ""
        version = versions.get(parent.document_version_id)
        block = f"【{title}】\n{parent.content.strip()}"
        separator_chars = 2 if blocks else 0
        available = max_context_chars - context_chars - separator_chars
        if available <= 0:
            break
        if len(block) > available:
            block = block[:available].rstrip()
            if not block:
                break

        citations.append(
            SourceCitation(
                title=title,
                source_uri=uri,
                kb_type=kb_type,
                parent_id=parent.id,
                chunk_id=child.chunk_id,
                score=round(child.score, 6),
                document_version_id=parent.document_version_id,
                file_name=version.pdf_file if version else "",
                checksum=version.checksum if version else "",
                page_start=parent.page_start,
                page_end=parent.page_end,
                section_path=parent.section_path,
            )
        )
        blocks.append(block)
        context_chars += separator_chars + len(block)
        if len(block) < len(f"【{title}】\n{parent.content.strip()}"):
            break

    return RetrievalResult(
        sources=citations,
        context="\n\n".join(blocks),
        parent_ids=[citation.parent_id for citation in citations],
    )


async def retrieve_parent_context(
    session: AsyncSession,
    query: str,
    kb_type: str,
    providers: Providers,
    recall_k: int = 12,
    rerank_k: int = 5,
    max_context_chars: int = MAX_CONTEXT_CHARS,
) -> RetrievalResult:
    """Run the full online retrieval pipeline for ``query`` against ``kb_type``."""
    vector = await providers.embeddings.embed_query(query)
    recalled = await rag_repo.vector_recall(session, vector, kb_type, recall_k)
    if not recalled:
        return RetrievalResult()

    candidates = [
        ChunkCandidate(chunk_id=chunk.id, parent_id=chunk.parent_id, content=chunk.content, score=score)
        for chunk, score in recalled
    ]
    hits = await providers.rerank.rerank(
        query, [c.content for c in candidates], rerank_k
    )
    hits = sorted(hits, key=lambda h: h.score, reverse=True)

    ranked: list[ChunkCandidate] = []
    for hit in hits:
        if 0 <= hit.index < len(candidates):
            ranked.append(replace(candidates[hit.index], score=hit.score))

    parent_ids = [c.parent_id for c in ranked]
    parents = await rag_repo.get_parents_by_ids(session, parent_ids)
    source_ids = [p.source_id for p in parents.values()]
    sources = await rag_repo.get_sources_by_ids(session, source_ids)
    version_ids = [
        p.document_version_id
        for p in parents.values()
        if p.document_version_id is not None
    ]
    versions = await rag_repo.get_versions_by_ids(session, version_ids)

    return expand_parents(
        ranked,
        parents,
        sources,
        versions,
        max_context_chars=max_context_chars,
    )
