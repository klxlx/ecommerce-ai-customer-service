"""Thread-safe in-process login protection and API rate limiting."""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable


class LoginGuard:
    """Track failures in a window and temporarily lock abusive keys."""

    def __init__(
        self,
        max_failures: int,
        window_seconds: int,
        lock_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_failures = max(1, max_failures)
        self._window_seconds = max(1, window_seconds)
        self._lock_seconds = max(1, lock_seconds)
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def _remaining_lock(self, key: str, now: float) -> float:
        locked_until = self._locked_until.get(key, 0.0)
        if locked_until <= now:
            if self._locked_until.pop(key, None) is not None:
                self._failures.pop(key, None)
            return 0.0
        return locked_until - now

    def record_failure(self, key: str) -> bool:
        """Record a failed login and return whether the key is now locked."""
        with self._lock:
            now = self._clock()
            if self._remaining_lock(key, now) > 0:
                return True

            cutoff = now - self._window_seconds
            failures = self._failures[key]
            while failures and failures[0] <= cutoff:
                failures.popleft()
            failures.append(now)
            if len(failures) >= self._max_failures:
                self._failures.pop(key, None)
                self._locked_until[key] = now + self._lock_seconds
                return True
            return False

    def is_locked(self, key: str) -> bool:
        with self._lock:
            return self._remaining_lock(key, self._clock()) > 0

    def retry_after(self, key: str) -> int:
        with self._lock:
            remaining = self._remaining_lock(key, self._clock())
            return max(0, math.ceil(remaining))

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class SlidingWindowRateLimiter:
    """A process-local sliding-window limiter safe for concurrent callers."""

    def __init__(
        self,
        limit: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = max(1, limit)
        self._window_seconds = max(1, window_seconds)
        self._clock = clock
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> RateLimitDecision:
        with self._lock:
            now = self._clock()
            requests = self._requests[key]
            cutoff = now - self._window_seconds
            while requests and requests[0] <= cutoff:
                requests.popleft()

            if len(requests) >= self._limit:
                retry_after = max(
                    1, math.ceil(requests[0] + self._window_seconds - now)
                )
                return RateLimitDecision(False, retry_after)

            requests.append(now)
            return RateLimitDecision(True)
