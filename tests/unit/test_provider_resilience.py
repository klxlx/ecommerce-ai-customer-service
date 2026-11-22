"""Provider timeout, retry, and total-budget behavior."""

from __future__ import annotations

import asyncio

import pytest

from app.core import resilience
from app.core.errors import (
    ProviderAuthenticationError,
    ProviderBudgetExceededError,
    ProviderConnectionError,
    ProviderStreamInterruptedError,
    ProviderTimeoutError,
)
from app.core.metrics import MetricsRegistry
from app.core.resilience import (
    ResiliencePolicy,
    execute_stream_with_resilience,
    execute_with_resilience,
)


@pytest.fixture(autouse=True)
def reset_circuits():
    resilience._circuits.clear()
    yield
    resilience._circuits.clear()


def _policy(**overrides) -> ResiliencePolicy:
    values = {
        "attempt_timeout_seconds": 0.2,
        "total_budget_seconds": 1.0,
        "max_retries": 2,
        "backoff_seconds": 0.0,
    }
    values.update(overrides)
    return ResiliencePolicy(**values)


@pytest.mark.anyio
async def test_transient_failure_retries_until_success():
    attempts = 0
    metrics = MetricsRegistry()

    async def flaky():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ProviderConnectionError("chat", "connection reset")
        return "ok"

    result = await execute_with_resilience(
        provider="chat",
        operation=flaky,
        policy=_policy(),
        metrics=metrics,
    )

    assert result == "ok"
    assert attempts == 3
    snapshot = metrics.snapshot()
    assert snapshot["counters"]["provider.retry.total"][
        'provider=chat,reason=connection'
    ] == 2


@pytest.mark.anyio
async def test_non_retryable_authentication_error_stops_immediately():
    attempts = 0

    async def unauthorized():
        nonlocal attempts
        attempts += 1
        raise ProviderAuthenticationError("chat", "bad key")

    with pytest.raises(ProviderAuthenticationError):
        await execute_with_resilience(
            provider="chat",
            operation=unauthorized,
            policy=_policy(max_retries=3),
        )

    assert attempts == 1


@pytest.mark.anyio
async def test_stream_retries_before_the_first_chunk():
    attempts = 0
    metrics = MetricsRegistry()

    async def flaky_stream():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ProviderTimeoutError("chat", "stream first token timeout")
        yield "ok"

    chunks = [
        chunk
        async for chunk in execute_stream_with_resilience(
            provider="chat",
            stream_factory=flaky_stream,
            policy=_policy(backoff_seconds=0.0),
            metrics=metrics,
        )
    ]

    assert "".join(chunks) == "ok"
    assert attempts == 3
    snapshot = metrics.snapshot()
    assert (
        snapshot["counters"]["provider.stream.attempt.total"]["provider=chat"]
        == 3
    )
    assert snapshot["counters"]["provider.stream.retry.total"][
        "provider=chat,reason=timeout"
    ] == 2


@pytest.mark.anyio
async def test_stream_does_not_retry_after_a_visible_chunk():
    attempts = 0
    visible = False

    async def failing_stream():
        nonlocal attempts
        attempts += 1
        yield "半截"
        raise ProviderTimeoutError("chat", "stream idle timeout")

    stream = execute_stream_with_resilience(
        provider="chat",
        stream_factory=failing_stream,
        policy=_policy(backoff_seconds=0.0),
        should_stop_retry=lambda: visible,
    )

    with pytest.raises(ProviderStreamInterruptedError) as caught:
        async for chunk in stream:
            assert chunk == "半截"
            visible = True

    assert attempts == 1
    assert caught.value.cause_category == "timeout"


@pytest.mark.anyio
async def test_single_attempt_timeout_is_retryable_then_mapped():
    attempts = 0

    async def slow():
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0.05)
        return "late"

    with pytest.raises(ProviderTimeoutError):
        await execute_with_resilience(
            provider="embedding",
            operation=slow,
            policy=_policy(
                attempt_timeout_seconds=0.005,
                total_budget_seconds=0.1,
                max_retries=1,
            ),
        )

    assert attempts == 2


@pytest.mark.anyio
async def test_total_budget_stops_retry_without_unbounded_wait():
    attempts = 0

    async def fail():
        nonlocal attempts
        attempts += 1
        raise ProviderConnectionError("rerank", "network down")

    with pytest.raises(ProviderBudgetExceededError):
        await execute_with_resilience(
            provider="rerank",
            operation=fail,
            policy=_policy(
                total_budget_seconds=0.02,
                max_retries=5,
                backoff_seconds=0.03,
            ),
        )

    assert attempts == 1

