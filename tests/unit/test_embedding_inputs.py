"""Embedding input normalization, size limits, and invalid-item isolation."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import get_settings
from app.core.errors import ProviderInvalidRequestError
from app.core.resilience import ResiliencePolicy
from app.rag.embeddings import (
    MAX_EMBEDDING_TEXT_CHARS,
    DashScopeEmbeddings,
    normalize_embedding_text,
    split_embedding_text,
)


def _settings():
    return get_settings().model_copy(
        update={
            "dashscope_api_key": "test-dashscope-key",
            "embedding_dimension": 4,
        }
    )


def _policy() -> ResiliencePolicy:
    return ResiliencePolicy(
        attempt_timeout_seconds=0.5,
        total_budget_seconds=2.0,
        max_retries=0,
        backoff_seconds=0,
    )


def _response(request: httpx.Request, texts: list[str]) -> httpx.Response:
    return httpx.Response(
        200,
        request=request,
        json={
            "output": {
                "embeddings": [
                    {"text_index": index, "embedding": [float(index)] * 4}
                    for index in range(len(texts))
                ]
            }
        },
    )


def test_normalize_embedding_text_removes_invisible_chars_and_noise():
    text = (
        "\ufeff  退款\t规则\u200b\r\n\r\n\r\n"
        "  仅适用于   官方渠道\u200d\x00  "
    )

    assert normalize_embedding_text(text) == (
        "退款 规则\n\n仅适用于 官方渠道"
    )


def test_normalize_embedding_text_rejects_content_that_becomes_empty():
    with pytest.raises(ValueError, match="empty"):
        normalize_embedding_text("\u200b\ufeff\x00\t\r\n")


def test_split_embedding_text_keeps_all_content_without_truncation():
    text = ("退款规则说明。" * 80) + "\n\n" + ("适用范围说明。" * 80)

    parts = split_embedding_text(text, max_chars=180)

    assert all(0 < len(part) <= 180 for part in parts)
    assert "".join(parts) == text
    assert len(parts) > 1


@pytest.mark.anyio
async def test_embed_documents_sends_clean_text():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return _response(request, payload["input"]["texts"])

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DashScopeEmbeddings(
        _settings(),
        client=http_client,
        policy=_policy(),
    )
    try:
        await provider.embed_documents(["  退款\u200b\t规则  "])
    finally:
        await http_client.aclose()

    assert requests[0]["input"]["texts"] == ["退款 规则"]


@pytest.mark.anyio
async def test_embed_documents_splits_long_text_without_truncation():
    requests: list[list[str]] = []
    text = "退款规则。" * (MAX_EMBEDDING_TEXT_CHARS // 5 + 40)

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]["texts"]
        requests.append(texts)
        return _response(request, texts)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DashScopeEmbeddings(
        _settings(),
        client=http_client,
        policy=_policy(),
    )
    try:
        vectors = await provider.embed_documents([text])
    finally:
        await http_client.aclose()

    assert len(vectors) == 1
    assert len(vectors[0]) == 4
    sent_fragments = [sent for batch in requests for sent in batch]
    assert len(sent_fragments) > 1
    assert "".join(sent_fragments) == text


@pytest.mark.anyio
async def test_invalid_batch_is_bisected_and_error_does_not_leak_content():
    requests: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]["texts"]
        requests.append(texts)
        if "bad-secret-content" in texts:
            return httpx.Response(
                400,
                request=request,
                json={"message": "invalid input"},
            )
        return _response(request, texts)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DashScopeEmbeddings(
        _settings(),
        client=http_client,
        policy=_policy(),
    )
    try:
        with pytest.raises(ProviderInvalidRequestError) as raised:
            await provider.embed_documents(
                ["first", "bad-secret-content", "third"]
            )
    finally:
        await http_client.aclose()

    assert ["bad-secret-content"] in requests
    assert "第 2 条" in raised.value.reason
    assert "bad-secret-content" not in raised.value.reason
    assert "invalid_request" in str(raised.value)
