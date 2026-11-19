"""Embedding provider abstraction and DashScope native implementation."""

from __future__ import annotations

import re
import unicodedata
from contextlib import asynccontextmanager
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

MAX_EMBEDDING_TEXT_CHARS = 1200
MAX_EMBEDDING_BATCH_ITEMS = 10
MAX_EMBEDDING_BATCH_CHARS = 5000
_EMBEDDING_BOUNDARIES = ("\n\n", "\n", "。", "！", "？", ".", "!", "?", ";", "；")


def normalize_embedding_text(text: str) -> str:
    """Remove invisible input noise and normalize whitespace for embedding."""
    if not isinstance(text, str):
        raise TypeError("embedding text must be a string")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        char
        for char in text
        if char in {"\n", "\t"} or not unicodedata.category(char).startswith("C")
    )
    lines: list[str] = []
    previous_blank = False
    for raw_line in text.split("\n"):
        line = re.sub(r"[ \t\f\v]+", " ", raw_line).strip()
        if line:
            lines.append(line)
            previous_blank = False
        elif lines and not previous_blank:
            lines.append("")
            previous_blank = True
    normalized = "\n".join(lines).strip()
    if not normalized:
        raise ValueError("embedding text is empty after normalization")
    return normalized


def split_embedding_text(text: str, *, max_chars: int) -> list[str]:
    """Split text at a natural boundary without dropping any content."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return [text] if text else []

    parts: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut = 0
        for boundary in _EMBEDDING_BOUNDARIES:
            index = window.rfind(boundary)
            if index >= 0:
                cut = max(cut, index + len(boundary))
        if cut < max_chars // 3:
            cut = max_chars
        parts.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        parts.append(remaining)
    return parts


class EmbeddingProvider(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class DashScopeEmbeddings:
    """Embed documents and queries via the DashScope native embedding API."""

    endpoint = (
        "https://dashscope.aliyuncs.com/api/v1/services/embeddings/"
        "text-embedding/text-embedding"
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
        self._dimension = settings.embedding_dimension
        self._client = client
        self._metrics = metrics
        self._policy = policy or ResiliencePolicy(
            attempt_timeout_seconds=settings.embedding_attempt_timeout_seconds,
            total_budget_seconds=settings.embedding_total_budget_seconds,
            max_retries=settings.provider_max_retries,
            backoff_seconds=settings.provider_retry_backoff_seconds,
        )

    @asynccontextmanager
    async def _http_client(self):
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(
            timeout=self._policy.attempt_timeout_seconds
        ) as client:
            yield client

    async def _embed(self, texts: list[str], text_type: str) -> list[list[float]]:
        async def operation() -> list[list[float]]:
            if not self._settings.dashscope_api_key:
                raise ProviderAuthenticationError(
                    "embedding", "DASHSCOPE_API_KEY is not configured"
                )
            try:
                async with self._http_client() as client:
                    resp = await client.post(
                        self.endpoint,
                        headers={
                            "Authorization": (
                                f"Bearer {self._settings.dashscope_api_key}"
                            )
                        },
                        json={
                            "model": self._settings.embedding_model,
                            "input": {"texts": texts},
                            "parameters": {
                                "text_type": text_type,
                                "dimension": self._dimension,
                            },
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
            except httpx.HTTPError as exc:
                raise map_httpx_error("embedding", exc) from exc

            try:
                embeddings = sorted(
                    data["output"]["embeddings"],
                    key=lambda item: item["text_index"],
                )
                vectors = [item["embedding"] for item in embeddings]
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderInvalidRequestError(
                    "embedding", "invalid embedding response"
                ) from exc
            if len(vectors) != len(texts) or any(
                len(vector) != self._dimension for vector in vectors
            ):
                raise ProviderInvalidRequestError(
                    "embedding", "unexpected embedding vector shape"
                )
            return vectors

        return await execute_with_resilience(
            provider="embedding",
            operation=operation,
            policy=self._policy,
            metrics=self._metrics,
        )

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        entries: list[tuple[int, str]] = []
        for position, text in enumerate(texts):
            normalized = _normalize_at_position(text, position)
            fragments = split_embedding_text(
                normalized,
                max_chars=MAX_EMBEDDING_TEXT_CHARS,
            )
            entries.extend((position, fragment) for fragment in fragments)

        vectors_by_position: list[list[list[float]]] = [[] for _ in texts]
        for batch in _embedding_batches(entries):
            vectors = await self._embed_with_bisect(batch, "document")
            for (position, _), vector in zip(batch, vectors, strict=True):
                vectors_by_position[position].append(vector)
        return [
            _mean_vector(vectors, position=position)
            for position, vectors in enumerate(vectors_by_position)
        ]

    async def embed_query(self, text: str) -> list[float]:
        normalized = _normalize_at_position(text, 0)
        fragments = split_embedding_text(
            normalized,
            max_chars=MAX_EMBEDDING_TEXT_CHARS,
        )
        entries = [(0, fragment) for fragment in fragments]
        vectors = await self._embed_with_bisect(entries, "query")
        return _mean_vector(vectors, position=0)

    async def _embed_with_bisect(
        self,
        entries: list[tuple[int, str]],
        text_type: str,
    ) -> list[list[float]]:
        """Retry a rejected multi-item request in smaller safe groups."""
        try:
            return await self._embed(
                [text for _, text in entries],
                text_type,
            )
        except ProviderInvalidRequestError as exc:
            if len(entries) == 1:
                position, text = entries[0]
                provider_message = _safe_provider_message(exc.reason)
                reason = (
                    f"第 {position + 1} 条（{len(text)} 字符）被向量服务拒绝"
                )
                if provider_message:
                    reason = f"{reason}：{provider_message}"
                raise ProviderInvalidRequestError("embedding", reason) from exc

            midpoint = len(entries) // 2
            left = await self._embed_with_bisect(entries[:midpoint], text_type)
            right = await self._embed_with_bisect(entries[midpoint:], text_type)
            return left + right


def _normalize_at_position(text: str, position: int) -> str:
    try:
        return normalize_embedding_text(text)
    except (TypeError, ValueError) as exc:
        raise ProviderInvalidRequestError(
            "embedding",
            f"第 {position + 1} 条为空或类型错误",
        ) from exc


def _embedding_batches(
    entries: list[tuple[int, str]],
) -> list[list[tuple[int, str]]]:
    batches: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    current_chars = 0
    for entry in entries:
        text_chars = len(entry[1])
        if current and (
            len(current) >= MAX_EMBEDDING_BATCH_ITEMS
            or current_chars + text_chars > MAX_EMBEDDING_BATCH_CHARS
        ):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(entry)
        current_chars += text_chars
    if current:
        batches.append(current)
    return batches


def _mean_vector(
    vectors: list[list[float]],
    *,
    position: int,
) -> list[float]:
    if not vectors:
        raise ProviderInvalidRequestError(
            "embedding",
            f"第 {position + 1} 条未返回向量",
        )
    if len(vectors) == 1:
        return vectors[0]
    dimensions = len(vectors[0])
    if any(len(vector) != dimensions for vector in vectors[1:]):
        raise ProviderInvalidRequestError(
            "embedding",
            f"第 {position + 1} 条拆分后的向量维度不一致",
        )
    return [
        sum(vector[index] for vector in vectors) / len(vectors)
        for index in range(dimensions)
    ]


def _safe_provider_message(reason: str) -> str:
    message = " ".join(str(reason).split())
    message = re.sub(r"https?://\S+", "<url>", message)
    return message[:240]
