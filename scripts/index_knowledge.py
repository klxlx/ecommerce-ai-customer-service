"""Index the official PDF registry into PostgreSQL + PGVector.

Usage:
    python -m scripts.index_knowledge

Requires DASHSCOPE_API_KEY for the embedding provider.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.config import get_settings
from app.db.session import create_session_factory
from app.rag.embeddings import DashScopeEmbeddings
from app.rag.indexer import IndexResult, index_registered_pdfs

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "data" / "knowledge" / "pdf_sources.json"


def format_index_summary(result: IndexResult) -> str:
    return (
        f"documents={result.documents_seen} "
        f"indexed={result.documents_indexed} "
        f"skipped={result.documents_skipped} "
        f"versions={result.versions_published} "
        f"chunks={result.chunks_indexed} "
        f"errors={len(result.errors)}"
    )


async def main() -> int:
    settings = get_settings()
    provider = DashScopeEmbeddings(settings)
    factory = create_session_factory(settings.database_url)
    async with factory() as session:
        result = await index_registered_pdfs(
            session,
            provider,
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            embedding_model=settings.embedding_model,
        )
    print(format_index_summary(result))
    for error in result.errors:
        print(f"  ERROR {error}", file=sys.stderr)
    return 1 if result.errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
