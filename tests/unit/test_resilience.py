"""Circuit breaker behaviour for provider calls."""

from __future__ import annotations

import pytest

from app.core import resilience
from app.core.errors import ProviderCircuitOpenError, ProviderError
from app.core.resilience import ResiliencePolicy, execute_with_resilience


@pytest.fixture(autouse=True)
def reset_circuits():
    resilience._circuits.clear()
    yield
    resilience._circuits.clear()


@pytest.mark.anyio
async def test_circuit_opens_after_three_final_failures():
    async def fail():
        raise ProviderError("test-provider", "boom")

    policy = ResiliencePolicy(
        attempt_timeout_seconds=0.5,
        total_budget_seconds=1.0,
        max_retries=0,
    )

    for _ in range(3):
        with pytest.raises(ProviderError) as error:
            await execute_with_resilience(
                provider="test-provider",
                operation=fail,
                policy=policy,
            )
        assert not isinstance(error.value, ProviderCircuitOpenError)

    with pytest.raises(ProviderCircuitOpenError):
        await execute_with_resilience(
            provider="test-provider",
            operation=fail,
            policy=policy,
        )


@pytest.mark.anyio
async def test_successful_call_resets_failure_count():
    async def fail():
        raise ProviderError("reset-provider", "boom")

    async def succeed():
        return "ok"

    policy = ResiliencePolicy(
        attempt_timeout_seconds=0.5,
        total_budget_seconds=1.0,
        max_retries=0,
    )

    for _ in range(2):
        with pytest.raises(ProviderError):
            await execute_with_resilience(
                provider="reset-provider",
                operation=fail,
                policy=policy,
            )

    assert await execute_with_resilience(
        provider="reset-provider",
        operation=succeed,
        policy=policy,
    ) == "ok"
    assert resilience._circuits["reset-provider"].failures == 0
