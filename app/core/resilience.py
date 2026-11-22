"""Bounded timeout, retry, and total-budget execution for providers."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, TypeVar

from app.core.errors import (
    ProviderBudgetExceededError,
    ProviderCircuitOpenError,
    ProviderError,
    ProviderStreamInterruptedError,
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


async def execute_stream_with_resilience(
    *,
    provider: str,
    stream_factory: Callable[[], AsyncIterator[T]],
    policy: ResiliencePolicy,
    metrics: MetricsRegistry | None = None,
    should_stop_retry: Callable[[], bool] | None = None,
    on_retry: Callable[[], None] | None = None,
) -> AsyncIterator[T]:
    """Consume a provider stream with retries before the first visible token.

    ``should_stop_retry`` is evaluated after a stream error and after the
    consumer has processed any yielded items. Callers use it to report whether
    the customer has already seen a token. Once it returns true, the stream is
    not retried; a ``ProviderStreamInterruptedError`` is raised so the caller
    can replace the bubble with a non-streaming completion instead of
    duplicating a partial answer.

    ``on_retry`` runs before a fresh attempt starts. It lets a caller that
    buffers tokens internally (for example for sentinel validation) discard a
    partial buffer before the retry.
    """
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
                provider, "stream total budget exhausted"
            )
            record_degraded(error)
            raise error

        if metrics:
            metrics.increment("provider.stream.attempt.total", provider=provider)

        iterator: AsyncIterator[T] | None = None
        first_item = True
        try:
            iterator = stream_factory()
            while True:
                remaining = policy.total_budget_seconds - (
                    loop.time() - started_at
                )
                if remaining <= 0:
                    raise ProviderBudgetExceededError(
                        provider, "stream total budget exceeded"
                    )
                if first_item:
                    first_timeout = min(
                        policy.attempt_timeout_seconds, remaining
                    )
                    try:
                        async with asyncio.timeout(first_timeout):
                            item = await iterator.__anext__()
                    except TimeoutError as exc:
                        raise ProviderTimeoutError(
                            provider, "stream first token timeout"
                        ) from exc
                    first_item = False
                else:
                    item = await iterator.__anext__()
                yield item
        except StopAsyncIteration:
            _record_success(provider)
            return
        except ProviderError as error:
            visible = bool(should_stop_retry and should_stop_retry())
            if visible:
                if metrics:
                    metrics.increment(
                        "provider.stream.partial_fail.total",
                        provider=provider,
                        reason=error.category,
                    )
                interrupted = ProviderStreamInterruptedError(
                    provider,
                    f"stream interrupted after visible token: {error.category}",
                    cause_category=error.category,
                )
                record_degraded(interrupted)
                raise interrupted from error
            if not error.retryable or attempt + 1 >= max_attempts:
                record_degraded(error)
                raise

            elapsed = loop.time() - started_at
            next_wait = max(0.0, policy.backoff_seconds)
            if elapsed + next_wait >= policy.total_budget_seconds:
                if metrics:
                    metrics.increment(
                        "provider.budget_exceeded.total", provider=provider
                    )
                budget_error = ProviderBudgetExceededError(
                    provider, "stream retry would exceed total budget"
                )
                record_degraded(budget_error)
                raise budget_error

            if metrics:
                metrics.increment(
                    "provider.stream.retry.total",
                    provider=provider,
                    reason=error.category,
                )
            if on_retry:
                on_retry()
            if next_wait:
                await asyncio.sleep(next_wait)
        finally:
            if iterator is not None:
                close = getattr(iterator, "aclose", None)
                if callable(close):
                    try:
                        await close()
                    except Exception:
                        pass

    error = ProviderBudgetExceededError(provider, "stream retry budget exhausted")
    record_degraded(error)
    raise error
