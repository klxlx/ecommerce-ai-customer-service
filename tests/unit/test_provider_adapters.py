"""Provider adapter contracts for error mapping, retries, and payloads."""

from __future__ import annotations

import json

import httpx
import pytest
from openai import AsyncOpenAI

from app.providers import chat as chat_module
from app.core.config import get_settings
from app.core.errors import (
    ProviderAuthenticationError,
    ProviderInvalidRequestError,
    ProviderTimeoutError,
)
from app.core.metrics import MetricsRegistry
from app.core.resilience import ResiliencePolicy
from app.providers.chat import OpenAICompatChat
from app.rag.embeddings import DashScopeEmbeddings
from app.rag.rerank import DashScopeReranker


def _settings():
    return get_settings().model_copy(
        update={
            "chat_api_key": "test-chat-key",
            "dashscope_api_key": "test-dashscope-key",
            "embedding_dimension": 1024,
        }
    )


def _policy(*, max_retries: int = 1) -> ResiliencePolicy:
    return ResiliencePolicy(
        attempt_timeout_seconds=0.5,
        total_budget_seconds=2.0,
        max_retries=max_retries,
        backoff_seconds=0,
    )


def _chat_client(handler) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key="test-key",
        base_url="https://chat.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _chat_completion(
    content: str = "ok", *, request: httpx.Request | None = None
) -> httpx.Response:
    return httpx.Response(
        200,
        request=request,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": "test-chat",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        },
    )


@pytest.mark.anyio
async def test_chat_without_api_key_fails_before_using_placeholder_client(
    monkeypatch,
):
    class PlaceholderClient:
        api_key = "not-configured"

        @property
        def chat(self):
            raise AssertionError("placeholder client must not be used")

    monkeypatch.setattr(
        chat_module,
        "AsyncOpenAI",
        lambda **kwargs: PlaceholderClient(),
    )
    settings = _settings().model_copy(update={"chat_api_key": ""})
    provider = OpenAICompatChat(settings)

    with pytest.raises(ProviderAuthenticationError):
        await provider.complete([{"role": "user", "content": "hello"}])


@pytest.mark.anyio
async def test_chat_maps_authentication_failure_without_retry():
    attempts = 0
    metrics = MetricsRegistry()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            401,
            request=request,
            json={
                "error": {
                    "message": "invalid api key",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_api_key",
                }
            },
        )

    client = _chat_client(handler)
    provider = OpenAICompatChat(
        _settings(),
        client=client,
        metrics=metrics,
        policy=_policy(max_retries=2),
    )
    try:
        with pytest.raises(ProviderAuthenticationError):
            await provider.complete([{"role": "user", "content": "hello"}])
    finally:
        await client.close()

    assert attempts == 1
    snapshot = metrics.snapshot()
    assert snapshot["counters"]["provider.request.total"]["provider=chat"] == 1
    assert snapshot["counters"]["provider.degraded.total"][
        "provider=chat,reason=authentication"
    ] == 1


@pytest.mark.anyio
async def test_chat_retries_transient_server_failure_then_succeeds():
    attempts = 0
    metrics = MetricsRegistry()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                503,
                request=request,
                json={
                    "error": {
                        "message": "temporarily unavailable",
                        "type": "server_error",
                        "param": None,
                        "code": None,
                    }
                },
            )
        return _chat_completion("recovered", request=request)

    client = _chat_client(handler)
    provider = OpenAICompatChat(
        _settings(), client=client, metrics=metrics, policy=_policy()
    )
    try:
        answer = await provider.complete([{"role": "user", "content": "hello"}])
    finally:
        await client.close()

    assert answer == "recovered"
    assert attempts == 2
    snapshot = metrics.snapshot()
    assert snapshot["counters"]["provider.request.total"]["provider=chat"] == 2
    assert snapshot["counters"]["provider.retry.total"][
        "provider=chat,reason=connection"
    ] == 1
    assert "provider.degraded.total" not in snapshot["counters"]


@pytest.mark.anyio
async def test_chat_can_override_model_for_specialized_agent():
    request_payload: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload.update(json.loads(request.content))
        return _chat_completion("ok", request=request)

    client = _chat_client(handler)
    provider = OpenAICompatChat(_settings(), client=client)
    try:
        answer = await provider.complete(
            [{"role": "user", "content": "hello"}],
            model="qwen-turbo",
        )
    finally:
        await client.close()

    assert answer == "ok"
    assert request_payload["model"] == "qwen-turbo"


@pytest.mark.anyio
async def test_chat_maps_timeout_and_records_each_timed_out_attempt():
    attempts = 0
    metrics = MetricsRegistry()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("provider timed out", request=request)

    client = _chat_client(handler)
    provider = OpenAICompatChat(
        _settings(), client=client, metrics=metrics, policy=_policy()
    )
    try:
        with pytest.raises(ProviderTimeoutError):
            await provider.complete([{"role": "user", "content": "hello"}])
    finally:
        await client.close()

    assert attempts == 2
    snapshot = metrics.snapshot()
    assert snapshot["counters"]["provider.timeout.total"]["provider=chat"] == 2
    assert snapshot["counters"]["provider.degraded.total"][
        "provider=chat,reason=timeout"
    ] == 1


@pytest.mark.anyio
async def test_embeddings_preserve_text_index_order_and_dimensions():
    metrics = MetricsRegistry()
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={
                "output": {
                    "embeddings": [
                        {"text_index": 1, "embedding": [1.0] * 1024},
                        {"text_index": 0, "embedding": [0.0] * 1024},
                    ]
                },
                "usage": {"total_tokens": 4},
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DashScopeEmbeddings(
        _settings(),
        client=http_client,
        metrics=metrics,
        policy=_policy(),
    )
    try:
        vectors = await provider.embed_documents(["first", "second"])
    finally:
        await http_client.aclose()

    assert requests[0]["parameters"]["dimension"] == 1024
    assert [len(vector) for vector in vectors] == [1024, 1024]
    assert vectors[0][0] == 0.0
    assert vectors[1][0] == 1.0
    assert metrics.snapshot()["counters"]["provider.request.total"][
        "provider=embedding"
    ] == 1


@pytest.mark.anyio
async def test_embeddings_retry_rate_limit_then_return_query_vector():
    attempts = 0
    metrics = MetricsRegistry()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                request=request,
                json={"message": "requests rate limited"},
            )
        return httpx.Response(
            200,
            request=request,
            json={
                "output": {
                    "embeddings": [
                        {"text_index": 0, "embedding": [0.25] * 1024}
                    ]
                }
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DashScopeEmbeddings(
        _settings(),
        client=http_client,
        metrics=metrics,
        policy=_policy(),
    )
    try:
        vector = await provider.embed_query("query")
    finally:
        await http_client.aclose()

    assert attempts == 2
    assert len(vector) == 1024
    snapshot = metrics.snapshot()
    assert snapshot["counters"]["provider.retry.total"][
        "provider=embedding,reason=rate_limit"
    ] == 1


@pytest.mark.anyio
async def test_rerank_orders_hits_by_score_and_keeps_documents_bound():
    metrics = MetricsRegistry()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "output": {
                    "results": [
                        {"index": 0, "relevance_score": 0.73},
                        {"index": 2, "relevance_score": 0.91},
                    ]
                }
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DashScopeReranker(
        _settings(),
        client=http_client,
        metrics=metrics,
        policy=_policy(),
    )
    documents = ["zero", "one", "two"]
    try:
        hits = await provider.rerank("query", documents, top_n=2)
    finally:
        await http_client.aclose()

    assert [(hit.index, hit.document, hit.score) for hit in hits] == [
        (2, "two", 0.91),
        (0, "zero", 0.73),
    ]
    assert metrics.snapshot()["counters"]["provider.request.total"][
        "provider=rerank"
    ] == 1


@pytest.mark.anyio
async def test_rerank_maps_invalid_request_without_retry():
    attempts = 0
    metrics = MetricsRegistry()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            400,
            request=request,
            json={"message": "invalid rerank request"},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DashScopeReranker(
        _settings(),
        client=http_client,
        metrics=metrics,
        policy=_policy(max_retries=2),
    )
    try:
        with pytest.raises(ProviderInvalidRequestError):
            await provider.rerank("query", ["one"], top_n=1)
    finally:
        await http_client.aclose()

    assert attempts == 1
    snapshot = metrics.snapshot()
    assert snapshot["counters"]["provider.request.total"]["provider=rerank"] == 1
    assert snapshot["counters"]["provider.degraded.total"][
        "provider=rerank,reason=invalid_request"
    ] == 1
