"""Unit tests for login protection and rate limiting."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.core.rate_limit import LoginGuard, SlidingWindowRateLimiter


def test_login_guard_locks_after_limit_and_clears_after_expiry():
    now = [100.0]
    guard = LoginGuard(
        max_failures=3,
        window_seconds=60,
        lock_seconds=30,
        clock=lambda: now[0],
    )

    assert guard.is_locked("alice|127.0.0.1") is False
    assert guard.record_failure("alice|127.0.0.1") is False
    assert guard.record_failure("alice|127.0.0.1") is False
    assert guard.record_failure("alice|127.0.0.1") is True
    assert guard.retry_after("alice|127.0.0.1") == 30

    now[0] += 31
    assert guard.is_locked("alice|127.0.0.1") is False
    assert guard.record_failure("alice|127.0.0.1") is False


def test_login_guard_ignores_failures_outside_window():
    now = [100.0]
    guard = LoginGuard(
        max_failures=2,
        window_seconds=10,
        lock_seconds=30,
        clock=lambda: now[0],
    )

    assert guard.record_failure("alice|127.0.0.1") is False
    now[0] += 11
    assert guard.record_failure("alice|127.0.0.1") is False
    assert guard.is_locked("alice|127.0.0.1") is False


def test_login_guard_success_clears_failures():
    now = [100.0]
    guard = LoginGuard(
        max_failures=3,
        window_seconds=60,
        lock_seconds=30,
        clock=lambda: now[0],
    )

    guard.record_failure("alice|127.0.0.1")
    guard.record_failure("alice|127.0.0.1")
    guard.reset("alice|127.0.0.1")

    assert guard.record_failure("alice|127.0.0.1") is False
    assert guard.record_failure("alice|127.0.0.1") is False
    assert guard.is_locked("alice|127.0.0.1") is False


def test_sliding_window_rate_limit_is_atomic_under_concurrency():
    limiter = SlidingWindowRateLimiter(
        limit=25,
        window_seconds=60,
        clock=lambda: 100.0,
    )

    with ThreadPoolExecutor(max_workers=16) as pool:
        decisions = list(pool.map(lambda _: limiter.check("user:1"), range(100)))

    assert sum(decision.allowed for decision in decisions) == 25
    assert sum(not decision.allowed for decision in decisions) == 75
    assert all(
        decision.retry_after_seconds == 60
        for decision in decisions
        if not decision.allowed
    )


def test_sliding_window_rate_limit_expires_old_requests():
    now = [100.0]
    limiter = SlidingWindowRateLimiter(
        limit=1,
        window_seconds=10,
        clock=lambda: now[0],
    )

    assert limiter.check("user:1").allowed is True
    assert limiter.check("user:1").allowed is False
    now[0] += 10
    assert limiter.check("user:1").allowed is True

