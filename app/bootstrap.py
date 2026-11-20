"""Build the provider container from settings."""

from __future__ import annotations

from app.agents.registry import build_agent_registry
from app.core.config import Settings
from app.core.metrics import MetricsRegistry
from app.graph.container import Container
from app.providers.chat import OpenAICompatChat
from app.providers.dashscope import build_providers
from app.providers.mock import MockChat, MockEmbeddings, MockReranker
from app.rag.embeddings import DashScopeEmbeddings
from app.rag.retriever import Providers
from app.services.vision import VisionUnavailable, build_vision_analyzer
from app.tools.local_time import get_local_time
from app.tools.weather import MCPWeatherClient


def build_container(
    settings: Settings, metrics: MetricsRegistry | None = None
) -> Container:
    agents = build_agent_registry(settings)
    weather = MCPWeatherClient(settings)
    try:
        vision = build_vision_analyzer(settings)
    except VisionUnavailable:
        vision = None
    if settings.mock_providers:
        return Container(
            chat=MockChat(),
            providers=Providers(
                embeddings=MockEmbeddings(), rerank=MockReranker()
            ),
            settings=settings,
            metrics=metrics,
            agents=agents,
            weather=weather,
            local_time=get_local_time,
            vision=vision,
            cache_embeddings=MockEmbeddings(),
        )
    providers = build_providers(settings, metrics=metrics)
    cache_embeddings = providers.embeddings
    if (
        settings.semantic_cache_embedding_model_name
        != settings.embedding_model
    ):
        cache_settings = settings.model_copy(
            update={
                "embedding_model": settings.semantic_cache_embedding_model_name
            }
        )
        cache_embeddings = DashScopeEmbeddings(cache_settings, metrics=metrics)
    return Container(
        chat=OpenAICompatChat(settings, metrics=metrics),
        providers=providers,
        settings=settings,
        metrics=metrics,
        agents=agents,
        weather=weather,
        local_time=get_local_time,
        vision=vision,
        cache_embeddings=cache_embeddings,
    )
