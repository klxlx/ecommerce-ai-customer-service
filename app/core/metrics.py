"""Small process-local metrics registry for operational visibility."""

from __future__ import annotations

import threading
from collections import defaultdict


class MetricsRegistry:
    """Collect counters and timings without requiring external infrastructure."""

    def __init__(self) -> None:
        self._counters: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self._timings: dict[str, dict[str, float]] = defaultdict(
            lambda: {"count": 0, "sum_ms": 0.0}
        )
        self._lock = threading.Lock()

    @staticmethod
    def _label_key(**labels: str) -> str:
        return ",".join(f"{name}={value}" for name, value in labels.items())

    def increment(self, name: str, **labels: str) -> None:
        key = self._label_key(**labels) if labels else "value"
        with self._lock:
            self._counters[name][key] += 1

    def observe(self, name: str, value_ms: float) -> None:
        with self._lock:
            timing = self._timings[name]
            timing["count"] += 1
            timing["sum_ms"] += float(value_ms)

    def snapshot(self) -> dict:
        with self._lock:
            counters = {
                name: dict(values) for name, values in self._counters.items()
            }
            timings = {}
            for name, timing in self._timings.items():
                count = int(timing["count"])
                total = round(float(timing["sum_ms"]), 3)
                timings[name] = {
                    "count": count,
                    "sum_ms": total,
                    "avg_ms": round(total / count, 3) if count else 0.0,
                }
        return {"counters": counters, "timings": timings}
