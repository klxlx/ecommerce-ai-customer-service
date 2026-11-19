"""Reranking provider abstraction and DashScope native implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import Settings
from app.core.errors import (
    ProviderAuthenticationError,
    ProviderInvalidRequestError,
    map_httpx_error,
)
from app.core.metrics import MetricsRegistry
from app.core.resilience import (
    ResiliencePolicy,
    execute_with_resilience,
)


@dataclass
class RerankHit:
    index: int
    score: float
    document: str


class RerankProvider(Protocol):
    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[RerankHit]: ...


class DashScopeReranker:
    """Rerank documents via the DashScope native text-rerank API."""

    endpoint = (
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/"
        "text-rerank/text-rerank"
    )

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        metrics: MetricsRegistry | None = None,
        policy: ResiliencePolicy | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._metrics = metrics
        self._policy = policy or ResiliencePolicy(
            attempt_timeout_seconds=settings.rerank_attempt_timeout_seconds,
            total_budget_seconds=settings.rerank_total_budget_seconds,
            max_retries=settings.provider_max_retries,
            backoff_seconds=settings.provider_retry_backoff_seconds,
        )

    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[RerankHit]:
        if not documents:
            return []
        async def operation() -> list[RerankHit]:
            if not self._settings.dashscope_api_key:
                raise ProviderAuthenticationError(
                    "rerank", "DASHSCOPE_API_KEY is not configured"
                )
            try:
                if self._client is not None:
                    data = await self._request(self._client, query, documents, top_n)
                else:
                    async with httpx.AsyncClient(
                        timeout=self._policy.attempt_timeout_seconds
                    ) as client:
                        data = await self._request(
                            client, query, documents, top_n
                        )
            except httpx.HTTPError as exc:
                raise map_httpx_error("rerank", exc) from exc

            try:
                hits = [
                    RerankHit(
                        index=int(item["index"]),
                        score=float(item["relevance_score"]),
                        document=documents[int(item["index"])],
                    )
                    for item in data["output"]["results"]
                ]
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ProviderInvalidRequestError(
                    "rerank", "invalid rerank response"
                ) from exc
            return sorted(
                hits, key=lambda hit: hit.score, reverse=True
            )[: min(top_n, len(documents))]

        return await execute_with_resilience(
            provider="rerank",
            operation=operation,
            policy=self._policy,
            metrics=self._metrics,
        )

    async def _request(
        self,
        client: httpx.AsyncClient,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> dict:
        resp = await client.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self._settings.dashscope_api_key}"
            },
            json={
                "model": self._settings.rerank_model,
                "input": {"query": query, "documents": documents},
                "parameters": {"top_n": min(top_n, len(documents))},
            },
        )
        resp.raise_for_status()
        return resp.json()
