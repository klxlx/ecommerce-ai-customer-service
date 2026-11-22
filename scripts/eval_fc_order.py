"""Offline evaluator for the read-only order Function Calling planner.

This script exercises the real chat model but never touches the database:
tool execution is replaced by a schema validation stub. It is intentionally
separate from pytest so CI remains hermetic.

Usage:
    .\\.venv\\Scripts\\python.exe -m scripts.eval_fc_order
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.core.config import get_settings
from app.graph import tool_planner as planner_module
from app.graph.tool_planner import run_order_tool_loop
from app.prompts.tool_planner import build_tool_planner_messages
from app.providers.chat import OpenAICompatChat
from app.tools.registry import get_tool
from app.tools.runtime import ToolResult, validate_arguments

DEFAULT_CASES = Path("tests/fixtures/fc_order_eval.json")


async def _dry_run_tool(name, *, session, user_id, arguments, metrics=None):
    definition = get_tool(name)
    if definition is None:
        return ToolResult(tool=name, status="not_allowed")
    error = validate_arguments(definition.parameters, arguments)
    if error is not None:
        return ToolResult(tool=name, status="invalid_args", error_type=error)
    return ToolResult(
        tool=name,
        status="ok",
        data={"dry_run": True},
        evidence={"tool": name, "status": "ok"},
    )


async def _evaluate(cases_path: Path) -> int:
    settings = get_settings()
    if not settings.chat_api_key:
        print("CHAT_API_KEY 未配置，无法运行真实模型评测。")
        return 2
    settings = settings.model_copy(
        update={"fc_readonly_enabled": True, "fc_max_steps": 1}
    )
    chat = OpenAICompatChat(settings)
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    planner_module.execute_tool = _dry_run_tool  # type: ignore[assignment]

    selection_ok = 0
    argument_ok = 0
    argument_total = 0
    mismatches: list[str] = []
    for case in cases:
        expected = case["expected"]
        messages = build_tool_planner_messages(
            case.get("history", []),
            case["message"],
            slots=case.get("slots", {}),
        )
        try:
            plan = await run_order_tool_loop(
                chat=chat,
                settings=settings,
                messages=messages,
                session=None,
                user_id=1,
            )
        except Exception as exc:
            mismatches.append(f"{case['id']}: exception {type(exc).__name__}")
            continue

        actual_tool = (
            plan.tool_calls[0]["name"] if plan.tool_calls else None
        )
        if actual_tool == expected["tool"]:
            selection_ok += 1
        else:
            mismatches.append(
                f"{case['id']}: expected={expected['tool']} actual={actual_tool} "
                f"fallback={plan.fallback_reason}"
            )

        if expected["tool"] is not None:
            argument_total += 1
            actual_args = (
                plan.tool_calls[0]["arguments"] if plan.tool_calls else None
            )
            if actual_args == expected["arguments"]:
                argument_ok += 1
            else:
                mismatches.append(
                    f"{case['id']}: expected_args={expected['arguments']} "
                    f"actual_args={actual_args}"
                )

    total = len(cases)
    selection_rate = selection_ok / total if total else 0.0
    argument_rate = argument_ok / argument_total if argument_total else 1.0
    print(f"cases={total} selection={selection_ok}/{total} ({selection_rate:.1%})")
    print(
        f"arguments={argument_ok}/{argument_total} ({argument_rate:.1%})"
    )
    for mismatch in mismatches:
        print(f"- {mismatch}")

    if selection_rate < 0.90 or argument_rate < 0.95:
        print("评测未达门槛：selection >= 90%，arguments >= 95%。")
        return 1
    print("评测通过。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    args = parser.parse_args()
    return asyncio.run(_evaluate(args.cases))


if __name__ == "__main__":
    raise SystemExit(main())
