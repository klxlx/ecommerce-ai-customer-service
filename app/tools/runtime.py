"""Bounded execution wrapper for allowlisted tools.

Every model-selected tool call goes through this runtime. The runtime owns
argument validation, read-only enforcement, per-tool timeout/retry/circuit
breaking and the structured evidence returned to the graph.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.exc import InterfaceError, OperationalError

from app.core.metrics import MetricsRegistry
from app.tools.registry import ToolDefinition, ToolPolicy, get_tool


@dataclass
class ToolResult:
    tool: str
    status: str
    data: Any = None
    evidence: dict[str, Any] = field(default_factory=dict)
    error_type: str = ""
    call_id: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "not_found"}

    def to_message(self) -> str:
        payload = {
            "tool": self.tool,
            "status": self.status,
            "data": self.data,
            "evidence": self.evidence,
        }
        return json.dumps(payload, ensure_ascii=False, default=str)


@dataclass
class _ToolCircuit:
    failures: int = 0
    opened_at: float | None = None


_TOOL_CIRCUITS: dict[str, _ToolCircuit] = {}


def reset_tool_circuits() -> None:
    """Test helper: clear process-local circuit state."""
    _TOOL_CIRCUITS.clear()


def validate_arguments(
    parameters: dict[str, Any], arguments: Any
) -> str | None:
    """Validate the small JSON-schema subset used by our tools."""
    if not isinstance(arguments, dict):
        return "arguments_not_object"
    if "user_id" in arguments or "session" in arguments:
        return "identity_override_rejected"

    properties = parameters.get("properties", {})
    required = parameters.get("required", [])
    for name in required:
        value = arguments.get(name)
        if value is None or (
            isinstance(value, str) and not value.strip()
        ):
            return f"missing_{name}"

    if parameters.get("additionalProperties") is False:
        extra = set(arguments) - set(properties)
        if extra:
            return f"unexpected_{sorted(extra)[0]}"

    for name, value in arguments.items():
        spec = properties.get(name, {})
        expected_type = spec.get("type")
        if expected_type and not _type_matches(value, expected_type):
            return f"type_{name}"
        pattern = spec.get("pattern")
        if pattern and isinstance(value, str):
            if re.fullmatch(pattern, value) is None:
                return f"pattern_{name}"
        enum_values = spec.get("enum")
        if enum_values and value not in enum_values:
            return f"enum_{name}"
    return None


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError)):
        return True
    return isinstance(exc, (OperationalError, InterfaceError))


def _metric(metrics: MetricsRegistry | None, name: str, **labels: str) -> None:
    if metrics is not None:
        metrics.increment(name, **labels)


def _observe(metrics: MetricsRegistry | None, name: str, value_ms: float) -> None:
    if metrics is not None:
        metrics.observe(name, value_ms)


def _circuit_result(
    definition: ToolDefinition, policy: ToolPolicy
) -> ToolResult | None:
    circuit = _TOOL_CIRCUITS.get(definition.name)
    if circuit is None or circuit.opened_at is None:
        return None
    if time.monotonic() - circuit.opened_at < policy.cooldown_seconds:
        return ToolResult(
            tool=definition.name,
            status="circuit_open",
            evidence={
                "tool": definition.name,
                "status": "circuit_open",
                "source": definition.source,
                "read_only": definition.read_only,
                "attempts": 0,
            },
        )
    circuit.failures = 0
    circuit.opened_at = None
    return None


def _record_success(definition: ToolDefinition) -> None:
    circuit = _TOOL_CIRCUITS.get(definition.name)
    if circuit is not None:
        circuit.failures = 0
        circuit.opened_at = None


def _record_failure(definition: ToolDefinition, policy: ToolPolicy) -> None:
    circuit = _TOOL_CIRCUITS.setdefault(definition.name, _ToolCircuit())
    circuit.failures += 1
    if circuit.failures >= policy.circuit_threshold:
        circuit.opened_at = time.monotonic()


def _base_evidence(
    definition: ToolDefinition,
    status: str,
    attempts: int,
    latency_ms: float,
) -> dict[str, Any]:
    return {
        "tool": definition.name,
        "status": status,
        "source": definition.source,
        "read_only": definition.read_only,
        "attempts": attempts,
        "latency_ms": round(latency_ms, 3),
    }


async def execute_tool(
    name: str,
    *,
    session: Any,
    user_id: int,
    arguments: Any,
    metrics: MetricsRegistry | None = None,
) -> ToolResult:
    """Execute one allowlisted read-only tool with bounded resilience."""
    definition = get_tool(name)
    if definition is None:
        _metric(metrics, "tool.call.total", tool=name, outcome="not_allowed")
        _metric(metrics, "tool.failure.total", tool=name, reason="not_allowed")
        return ToolResult(
            tool=name,
            status="not_allowed",
            evidence={
                "tool": name,
                "status": "not_allowed",
                "source": "registry",
                "read_only": False,
                "attempts": 0,
            },
        )
    if not definition.read_only:
        _metric(metrics, "tool.call.total", tool=name, outcome="not_allowed")
        _metric(metrics, "tool.failure.total", tool=name, reason="not_allowed")
        return ToolResult(
            tool=name,
            status="not_allowed",
            evidence={
                "tool": name,
                "status": "not_allowed",
                "source": definition.source,
                "read_only": False,
                "attempts": 0,
            },
        )

    validation_error = validate_arguments(definition.parameters, arguments)
    if validation_error is not None:
        _metric(metrics, "tool.call.total", tool=name, outcome="invalid_args")
        _metric(metrics, "tool.failure.total", tool=name, reason="invalid_args")
        return ToolResult(
            tool=name,
            status="invalid_args",
            evidence={
                "tool": name,
                "status": "invalid_args",
                "source": definition.source,
                "read_only": True,
                "attempts": 0,
            },
            error_type=validation_error,
        )

    policy = definition.policy
    circuit_result = _circuit_result(definition, policy)
    if circuit_result is not None:
        _metric(metrics, "tool.call.total", tool=name, outcome="circuit_open")
        _metric(metrics, "tool.circuit_open.total", tool=name)
        _metric(metrics, "tool.failure.total", tool=name, reason="circuit_open")
        return circuit_result

    attempts = 0
    started = time.perf_counter()
    last_error = ""
    last_status = "error"
    for attempt in range(policy.max_retries + 1):
        attempts = attempt + 1
        try:
            async with asyncio.timeout(policy.timeout_seconds):
                if session is not None and hasattr(session, "begin_nested"):
                    async with session.begin_nested():
                        raw = await definition.handler(
                            session=session,
                            user_id=user_id,
                            **arguments,
                        )
                else:
                    raw = await definition.handler(
                        session=session,
                        user_id=user_id,
                        **arguments,
                    )
            data = (
                definition.serializer(raw)
                if definition.serializer is not None
                else raw
            )
            status = "ok" if data is not None else "not_found"
            _record_success(definition)
            latency_ms = (time.perf_counter() - started) * 1000
            _metric(metrics, "tool.call.total", tool=name, outcome=status)
            _observe(metrics, "tool.latency_ms", latency_ms)
            return ToolResult(
                tool=name,
                status=status,
                data=data,
                evidence=_base_evidence(
                    definition, status, attempts, latency_ms
                ),
            )
        except Exception as exc:
            last_error = type(exc).__name__
            last_status = "timeout" if isinstance(
                exc, (TimeoutError, asyncio.TimeoutError)
            ) else "error"
            if attempt < policy.max_retries and _is_retryable(exc):
                await asyncio.sleep(policy.retry_backoff_seconds)
                continue
            break

    _record_failure(definition, policy)
    latency_ms = (time.perf_counter() - started) * 1000
    _metric(metrics, "tool.call.total", tool=name, outcome=last_status)
    _metric(metrics, "tool.failure.total", tool=name, reason=last_status)
    _observe(metrics, "tool.latency_ms", latency_ms)
    return ToolResult(
        tool=name,
        status=last_status,
        evidence=_base_evidence(
            definition, last_status, attempts, latency_ms
        ),
        error_type=last_error,
    )


__all__ = [
    "ToolResult",
    "execute_tool",
    "reset_tool_circuits",
    "validate_arguments",
]
