"""Bounded timeout, retry, and total-budget execution for providers."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

from app.core.errors import (
    ProviderBudgetExceededError,
    ProviderCircuitOpenError,
    ProviderError,
    ProviderTimeoutError,
)
from app.core.metrics import MetricsRegistry

T = TypeVar("T")

CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_COOLDOWN_SECONDS = 30.0


@dataclass
class _Circuit:
    failures: int = 0
    opened_at: float | None = None


_circuits: dict[str, _Circuit] = {}


def _before_call(provider: str, metrics: MetricsRegistry | None) -> None:
    circuit = _circuits.setdefault(provider, _Circuit())
    if circuit.opened_at is None:
        return
    if time.monotonic() - circuit.opened_at >= CIRCUIT_COOLDOWN_SECONDS:
        circuit.failures = 0
        circuit.opened_at = None
        return
    if metrics:
        metrics.increment("provider.circuit_open.total", provider=provider)
    raise ProviderCircuitOpenError(provider, "circuit open")


def _record_success(provider: str) -> None:
    circuit = _circuits.setdefault(provider, _Circuit())
    circuit.failures = 0
    circuit.opened_at = None


def _record_final_failure(provider: str) -> None:
    circuit = _circuits.setdefault(provider, _Circuit())
    circuit.failures += 1
    if circuit.failures >= CIRCUIT_FAILURE_THRESHOLD:
        circuit.opened_at = time.monotonic()


@dataclass(frozen=True)
class ResiliencePolicy:
    attempt_timeout_seconds: float
    total_budget_seconds: float
    max_retries: int
    backoff_seconds: float = 0.0


async def execute_with_resilience(
    *,
    provider: str,
    operation: Callable[[], Awaitable[T]],
    policy: ResiliencePolicy,
    metrics: MetricsRegistry | None = None,
) -> T:
    """Execute one provider operation within explicit attempt and total limits."""
    _before_call(provider, metrics)
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    max_attempts = max(0, policy.max_retries) + 1

    def record_degraded(error: ProviderError) -> None:
        _record_final_failure(provider)
        if metrics and not error.degraded_recorded:
            metrics.increment(
                "provider.degraded.total",
                provider=error.provider,
                reason=error.category,
            )
            error.degraded_recorded = True

    for attempt in range(max_attempts):
        elapsed = loop.time() - started_at
        remaining_budget = policy.total_budget_seconds - elapsed
        if remaining_budget <= 0:
            if metrics:
                metrics.increment(
                    "provider.budget_exceeded.total", provider=provider
                )
            error = ProviderBudgetExceededError(
                provider, "total budget exhausted"
            )
            record_degraded(error)
            raise error

        attempt_timeout = min(policy.attempt_timeout_seconds, remaining_budget)
        if metrics:
            metrics.increment("provider.request.total", provider=provider)

        try:
            result = await asyncio.wait_for(
                operation(), timeout=attempt_timeout
            )
            _record_success(provider)
            return result
        except (asyncio.TimeoutError, TimeoutError) as exc:
            error: ProviderError = ProviderTimeoutError(provider, str(exc))
            if metrics:
                metrics.increment("provider.timeout.total", provider=provider)
        except ProviderError as exc:
            error = exc
            if metrics and isinstance(error, ProviderTimeoutError):
                metrics.increment("provider.timeout.total", provider=provider)
            if not error.retryable or attempt + 1 >= max_attempts:
                record_degraded(error)
                raise

        if not error.retryable or attempt + 1 >= max_attempts:
            record_degraded(error)
            raise error

        elapsed = loop.time() - started_at
        next_wait = max(0.0, policy.backoff_seconds)
        if elapsed + next_wait >= policy.total_budget_seconds:
            if metrics:
                metrics.increment(
                    "provider.budget_exceeded.total", provider=provider
                )
            budget_error = ProviderBudgetExceededError(
                provider, "retry would exceed total budget"
            )
            record_degraded(budget_error)
            raise budget_error

        if metrics:
            metrics.increment(
                "provider.retry.total",
                provider=provider,
                reason=error.category,
            )
        if next_wait:
            await asyncio.sleep(next_wait)

    error = ProviderBudgetExceededError(provider, "retry budget exhausted")
    record_degraded(error)
    raise error
