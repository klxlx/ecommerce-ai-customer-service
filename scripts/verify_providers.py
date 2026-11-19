"""Verify real chat, embedding, and rerank providers without leaking content.

Usage:
    python -m scripts.verify_providers

The command intentionally bypasses mock providers. Missing credentials are
reported as ``not_configured`` and produce a non-zero exit code.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.config import Settings, get_settings
from app.core.errors import ProviderError
from app.providers.chat import OpenAICompatChat
from app.rag.embeddings import DashScopeEmbeddings
from app.rag.rerank import DashScopeReranker

CHAT_PROMPT = "Reply with OK."
EMBEDDING_PROMPT = "E-commerce customer service provider verification."
RERANK_QUERY = "How should a refund request be handled?"
RERANK_DOCUMENTS = [
    "Refund requests must be reviewed by a human agent.",
    "Read the product manual before first use.",
    "Logistics status is retrieved from the order database.",
]


async def _timed(
    operation: Callable[[], Awaitable[Any]],
) -> tuple[Any | None, float, bool, str | None, bool]:
    started_at = time.perf_counter()
    try:
        result = await operation()
    except ProviderError as exc:
        return None, _elapsed_ms(started_at), True, exc.category, exc.retryable
    except Exception:
        return None, _elapsed_ms(started_at), True, "unexpected", False
    return result, _elapsed_ms(started_at), False, None, False


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 3)


def _error_result(
    *,
    model: str,
    elapsed_ms: float,
    category: str,
    retryable: bool,
) -> dict[str, Any]:
    return {
        "status": "error",
        "model": model,
        "elapsed_ms": elapsed_ms,
        "error_category": category,
        "retryable": retryable,
    }


async def verify_providers(
    settings: Settings,
    *,
    chat: Any | None = None,
    embeddings: Any | None = None,
    reranker: Any | None = None,
) -> dict[str, dict[str, Any]]:
    """Run real provider checks and return a sanitized metadata report."""
    report: dict[str, dict[str, Any]] = {}

    if not settings.chat_configured:
        report["chat"] = {
            "status": "not_configured",
            "model": settings.chat_model,
        }
    else:
        chat_provider = chat or OpenAICompatChat(settings)
        _, elapsed_ms, failed, category, retryable = await _timed(
            lambda: chat_provider.complete(
                [{"role": "user", "content": CHAT_PROMPT}]
            )
        )
        report["chat"] = (
            _error_result(
                model=settings.chat_model,
                elapsed_ms=elapsed_ms,
                category=category or "unexpected",
                retryable=retryable,
            )
            if failed
            else {
                "status": "ok",
                "model": settings.chat_model,
                "elapsed_ms": elapsed_ms,
            }
        )

    dashscope_configured = settings.embedding_configured
    if not dashscope_configured:
        report["embedding"] = {
            "status": "not_configured",
            "model": settings.embedding_model,
        }
    else:
        embedding_provider = embeddings or DashScopeEmbeddings(settings)
        vector, elapsed_ms, failed, category, retryable = await _timed(
            lambda: embedding_provider.embed_query(EMBEDDING_PROMPT)
        )
        report["embedding"] = (
            _error_result(
                model=settings.embedding_model,
                elapsed_ms=elapsed_ms,
                category=category or "unexpected",
                retryable=retryable,
            )
            if failed
            else {
                "status": "ok",
                "model": settings.embedding_model,
                "elapsed_ms": elapsed_ms,
                "dimensions": len(vector),
            }
        )

    if not dashscope_configured:
        report["rerank"] = {
            "status": "not_configured",
            "model": settings.rerank_model,
        }
    else:
        rerank_provider = reranker or DashScopeReranker(settings)
        hits, elapsed_ms, failed, category, retryable = await _timed(
            lambda: rerank_provider.rerank(
                RERANK_QUERY,
                RERANK_DOCUMENTS,
                top_n=len(RERANK_DOCUMENTS),
            )
        )
        report["rerank"] = (
            _error_result(
                model=settings.rerank_model,
                elapsed_ms=elapsed_ms,
                category=category or "unexpected",
                retryable=retryable,
            )
            if failed
            else {
                "status": "ok",
                "model": settings.rerank_model,
                "elapsed_ms": elapsed_ms,
                "count": len(hits),
                "order": [hit.index for hit in hits],
                "scores": [round(hit.score, 6) for hit in hits],
            }
        )

    return report


def verification_exit_code(report: dict[str, dict[str, Any]]) -> int:
    """Return 0 for all verified, 1 for failures, and 2 for missing keys."""
    statuses = {result["status"] for result in report.values()}
    if "error" in statuses:
        return 1
    if statuses == {"ok"}:
        return 0
    return 2


async def main() -> int:
    report = await verify_providers(get_settings())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return verification_exit_code(report)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
