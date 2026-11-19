"""Knowledge retrieval tool for the graph."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.retriever import Providers, retrieve_parent_context
from app.schemas.knowledge import RetrievalResult


async def retrieve_knowledge(
    session: AsyncSession,
    query: str,
    kb_type: str,
    providers: Providers,
    recall_k: int = 12,
    rerank_k: int = 5,
) -> RetrievalResult:
    return await retrieve_parent_context(
        session, query, kb_type, providers, recall_k, rerank_k
    )
