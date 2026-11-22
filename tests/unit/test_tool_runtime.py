"""Tool runtime contracts: allowlist, validation, retry and circuit state."""

from __future__ import annotations

import asyncio

import pytest

from app.tools import registry as registry_module
from app.tools.registry import ToolDefinition, ToolPolicy
from app.tools.runtime import execute_tool, reset_tool_circuits, validate_arguments


@pytest.fixture(autouse=True)
def _clear_tool_circuits():
    reset_tool_circuits()
    yield
    reset_tool_circuits()


def _demo_tool(
    handler,
    *,
    read_only: bool = True,
    policy: ToolPolicy | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name="demo_tool",
        description="test only",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=handler,
        read_only=read_only,
        source="test",
        policy=policy or ToolPolicy(timeout_seconds=0.5, max_retries=0),
        serializer=lambda value: value,
    )


def test_validate_arguments_rejects_identity_and_bad_shapes():
    schema = {
        "type": "object",
        "properties": {"order_no": {"type": "string", "pattern": r"^ORD-\d{4}$"}},
        "required": ["order_no"],
        "additionalProperties": False,
    }
    assert validate_arguments(schema, {"order_no": "ORD-5001"}) is None
    assert validate_arguments(schema, {"order_no": "ORD-5001", "user_id": 7}) == (
        "identity_override_rejected"
    )
    assert validate_arguments(schema, {}) == "missing_order_no"
    assert validate_arguments(schema, {"order_no": "bad"}) == "pattern_order_no"
    assert validate_arguments(schema, {"order_no": "ORD-5001", "x": 1}) == (
        "unexpected_x"
    )
    assert validate_arguments(schema, ["not", "object"]) == "arguments_not_object"


@pytest.mark.anyio
async def test_execute_tool_runs_only_allowlisted_read_only_tool():
    async def handler(*, session, user_id, value):
        return {"value": value, "user_id": user_id}

    registry_module.TOOL_REGISTRY["demo_tool"] = _demo_tool(handler)
    result = await execute_tool(
        "demo_tool",
        session=None,
        user_id=7,
        arguments={"value": 3},
    )
    assert result.status == "ok"
    assert result.data == {"value": 3, "user_id": 7}
    assert result.evidence["attempts"] == 1
    assert result.evidence["read_only"] is True

    write_tool = _demo_tool(handler, read_only=False)
    registry_module.TOOL_REGISTRY["write_tool"] = write_tool
    rejected = await execute_tool(
        "write_tool",
        session=None,
        user_id=7,
        arguments={"value": 3},
    )
    assert rejected.status == "not_allowed"
    assert rejected.evidence["read_only"] is False


@pytest.mark.anyio
async def test_execute_tool_retries_one_connection_failure_then_succeeds():
    attempts = 0

    async def handler(*, session, user_id, value):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("temporary")
        return {"value": value}

    policy = ToolPolicy(timeout_seconds=0.5, max_retries=1, retry_backoff_seconds=0)
    registry_module.TOOL_REGISTRY["demo_tool"] = _demo_tool(
        handler, policy=policy
    )
    result = await execute_tool(
        "demo_tool",
        session=None,
        user_id=7,
        arguments={"value": 3},
    )
    assert result.status == "ok"
    assert result.evidence["attempts"] == 2
    assert attempts == 2


@pytest.mark.anyio
async def test_execute_tool_opens_circuit_after_threshold():
    async def handler(*, session, user_id, value):
        raise RuntimeError("boom")

    policy = ToolPolicy(
        timeout_seconds=0.5,
        max_retries=0,
        circuit_threshold=2,
        cooldown_seconds=30,
    )
    registry_module.TOOL_REGISTRY["demo_tool"] = _demo_tool(
        handler, policy=policy
    )
    first = await execute_tool(
        "demo_tool", session=None, user_id=7, arguments={"value": 1}
    )
    second = await execute_tool(
        "demo_tool", session=None, user_id=7, arguments={"value": 1}
    )
    third = await execute_tool(
        "demo_tool", session=None, user_id=7, arguments={"value": 1}
    )
    assert first.status == "error"
    assert second.status == "error"
    assert third.status == "circuit_open"
    assert third.evidence["attempts"] == 0


@pytest.mark.anyio
async def test_execute_tool_timeout_is_structured_failure():
    async def handler(*, session, user_id, value):
        await asyncio.sleep(0.05)
        return {"value": value}

    policy = ToolPolicy(timeout_seconds=0.01, max_retries=0)
    registry_module.TOOL_REGISTRY["demo_tool"] = _demo_tool(
        handler, policy=policy
    )
    result = await execute_tool(
        "demo_tool", session=None, user_id=7, arguments={"value": 1}
    )
    assert result.status == "timeout"
    assert result.evidence["status"] == "timeout"
