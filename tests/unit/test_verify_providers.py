"""Contracts for the sanitized real-provider verification script."""

from __future__ import annotations

import json

import pytest

from app.core.config import get_settings
from app.core.errors import ProviderRateLimitError
from app.rag.rerank import RerankHit
from scripts.verify_providers import verification_exit_code, verify_providers


class ExplodingChat:
    async def complete(self, messages):
        raise AssertionError("unconfigured chat provider must not be called")


class ExplodingEmbeddings:
    async def embed_query(self, text):
        raise AssertionError("unconfigured embedding provider must not be called")


class ExplodingReranker:
    async def rerank(self, query, documents, top_n):
        raise AssertionError("unconfigured rerank provider must not be called")


class StaticChat:
    async def complete(self, messages):
        return "private model answer"


class StaticEmbeddings:
    async def embed_query(self, text):
        return [0.1, 0.2, 0.3]


class FailingReranker:
    async def rerank(self, query, documents, top_n):
        raise ProviderRateLimitError(
            "rerank", "private upstream response body"
        )


@pytest.mark.anyio
async def test_missing_keys_are_reported_without_calling_providers():
    settings = get_settings().model_copy(
        update={"chat_api_key": "", "dashscope_api_key": ""}
    )

    report = await verify_providers(
        settings,
        chat=ExplodingChat(),
        embeddings=ExplodingEmbeddings(),
        reranker=ExplodingReranker(),
    )

    assert report["chat"]["status"] == "not_configured"
    assert report["embedding"]["status"] == "not_configured"
    assert report["rerank"]["status"] == "not_configured"
    assert verification_exit_code(report) == 2


@pytest.mark.anyio
async def test_report_exposes_only_sanitized_metrics_and_domain_errors():
    settings = get_settings().model_copy(
        update={
            "chat_api_key": "test-chat-key",
            "dashscope_api_key": "test-dashscope-key",
        }
    )

    report = await verify_providers(
        settings,
        chat=StaticChat(),
        embeddings=StaticEmbeddings(),
        reranker=FailingReranker(),
    )

    assert report["chat"]["status"] == "ok"
    assert report["embedding"]["dimensions"] == 3
    assert set(report["chat"]) == {"status", "model", "elapsed_ms"}
    assert report["rerank"]["status"] == "error"
    assert report["rerank"]["error_category"] == "rate_limit"
    assert report["rerank"]["retryable"] is True
    assert verification_exit_code(report) == 1

    serialized = json.dumps(report, ensure_ascii=False)
    for secret in (
        "private model answer",
        "private upstream response body",
        "test-chat-key",
        "test-dashscope-key",
    ):
        assert secret not in serialized


@pytest.mark.anyio
async def test_rerank_success_reports_safe_order_and_scores():
    class StaticReranker:
        async def rerank(self, query, documents, top_n):
            return [
                RerankHit(index=1, score=0.9, document="private document"),
                RerankHit(index=0, score=0.4, document="another private document"),
            ][:top_n]

    settings = get_settings().model_copy(
        update={
            "chat_api_key": "test-chat-key",
            "dashscope_api_key": "test-dashscope-key",
        }
    )
    report = await verify_providers(
        settings,
        chat=StaticChat(),
        embeddings=StaticEmbeddings(),
        reranker=StaticReranker(),
    )

    assert report["rerank"] == {
        "status": "ok",
        "model": settings.rerank_model,
        "elapsed_ms": report["rerank"]["elapsed_ms"],
        "count": 2,
        "order": [1, 0],
        "scores": [0.9, 0.4],
    }
    assert "private document" not in json.dumps(report, ensure_ascii=False)
    assert verification_exit_code(report) == 0
