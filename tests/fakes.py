"""Deterministic fake providers for unit and integration tests."""

from __future__ import annotations

import hashlib
import random

from app.rag.rerank import RerankHit


class FakeChat:
    """Fake chat provider returning a canned completion and recording calls."""

    def __init__(self, response: str = "") -> None:
        self.response = response
        self.calls: list[list[dict]] = []

    async def complete(self, messages: list[dict]) -> str:
        self.calls.append(messages)
        return self.response


class FakeEmbeddings:
    """Deterministic 1024-dimension embeddings derived from the input text."""

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        seed = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)
        rng = random.Random(seed)
        return [rng.uniform(-1.0, 1.0) for _ in range(self.dim)]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class QueryVectorEmbeddings:
    """Embeddings whose query vector can be pinned for recall testing."""

    def __init__(self, query_vector: list[float], dim: int = 1024) -> None:
        self.query_vector = query_vector
        self.dim = dim

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dim for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self.query_vector


class FakeReranker:
    """Reranker that scores documents by a supplied ordering."""

    def __init__(self, order: list[int] | None = None) -> None:
        self.order = order  # indexes into the document list, in preferred order

    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[RerankHit]:
        if self.order is None:
            indexes = list(range(len(documents)))
        else:
            indexes = [i for i in self.order if i < len(documents)]
        indexes = indexes[:top_n]
        n = len(indexes)
        return [
            RerankHit(index=idx, score=1.0 - (rank / (n + 1)), document=documents[idx])
            for rank, idx in enumerate(indexes)
        ]
