"""Provider construction helpers for DashScope-native embedding and rerank."""

from __future__ import annotations

from app.core.config import Settings
from app.core.metrics import MetricsRegistry
from app.rag.embeddings import DashScopeEmbeddings
from app.rag.rerank import DashScopeReranker
from app.rag.retriever import Providers


def build_providers(
    settings: Settings, metrics: MetricsRegistry | None = None
) -> Providers:
    """Build the embedding and rerank providers from settings."""
    return Providers(
        embeddings=DashScopeEmbeddings(settings, metrics=metrics),
        rerank=DashScopeReranker(settings, metrics=metrics),
    )
