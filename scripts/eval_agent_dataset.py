"""Offline baseline evaluation for dataset-derived agent assets.

This script does not write business data. It calls the configured chat model
for intent/tool/sentinel evaluation and writes JSON/Markdown reports.

Usage:
    python -m scripts.eval_agent_dataset --limit 20
    python -m scripts.eval_agent_dataset
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from app.bootstrap import build_container
from app.core.config import get_settings
from app.graph.nodes import route_node
from app.services.sentinel import check_answer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "data" / "eval"

TOOL_SYSTEM = """你是电商客服工具决策评测器。根据用户诉求和工具目录，输出需要执行的完整工具动作序列。
只输出一个 JSON 对象：
{"actions":["工具名"],"reason":""}
如果无需工具，返回 {"actions":[],"reason":""}。

可用工具：
- query_order_status：查询订单状态与物流，通常第一步调用。
- check_return_policy：查询退货/换货政策及是否在售后窗口，需要先有订单状态。
- create_after_sales_request：创建售后单，必须在确认订单状态和政策后再调用。
不要编造工具名，不要把工具结果写进 actions。"""

TOOL_ERROR_SYSTEM = """你是电商客服工具决策评测器。当前场景是 query_order_status 已经返回错误。
只判断下一步是否应调用 create_after_sales_request：
- TIMEOUT / SERVICE_UNAVAILABLE / ORDER_NOT_FOUND 时不得创建售后，返回 {"action":"none"}。
- 如果可以安全创建售后，返回 {"action":"create_after_sales_request"}。
只输出 JSON。"""


def _read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def _json_object(text: str) -> dict | None:
    candidate = (text or "").strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:\w+)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    match = re.search(r"\{.*\}", candidate, re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _parse_tool_actions(text: str) -> list[str] | None:
    parsed = _json_object(text)
    if parsed is not None:
        actions = parsed.get("actions")
        if isinstance(actions, list):
            return [str(item) for item in actions]
        action = parsed.get("action")
        if isinstance(action, str):
            return [] if action == "none" else [action]
    actions = re.findall(r"Action:\s*(\w+)", text or "")
    return actions if actions else None


def _normalize_product(value: str) -> str:
    return re.sub(r"[\s，。！？、,.!?~～：:；;\"“”‘’]+", "", value).lower()


def _intent_matches(expected: str, actual: str | None, text: str) -> bool:
    """Allow the documented complaint-policy distinction without loosening."""
    if actual == expected:
        return True
    if expected == "after_sale" and actual == "after_sale":
        return True
    return False


async def eval_intent(container, records: list[dict], concurrency: int) -> dict:
    semaphore = asyncio.Semaphore(concurrency)
    results = []

    async def one(record: dict) -> dict:
        async with semaphore:
            try:
                state = {
                    "user_message": record["user"],
                    "history": [],
                    "slots": {},
                    "chat": container.chat,
                    "providers": container.providers,
                    "agents": container.agents,
                    "session": None,
                }
                output = await asyncio.wait_for(
                    route_node(state), timeout=25
                )
                actual_intent = output.get("intent")
                actual_slots = output.get("slots") or {}
                expected = record["expected"]
                ok = _intent_matches(
                    expected["intent"], actual_intent, record["user"]
                )
                action_expected = expected["slots"].get(
                    "after_sale_action", ""
                )
                action_actual = str(
                    actual_slots.get("after_sale_action", "")
                )
                action_ok = (
                    (not action_expected)
                    or action_expected == action_actual
                )
                product_expected = _normalize_product(
                    expected["slots"].get("product", "")
                )
                product_actual = _normalize_product(
                    str(actual_slots.get("product", ""))
                )
                product_ok = (
                    (not product_expected)
                    or product_expected in product_actual
                    or product_actual in product_expected
                )
            except Exception as exc:
                return {
                    "id": record["id"],
                    "user": record["user"],
                    "expected": record["expected"]["intent"],
                    "actual": f"ERROR:{type(exc).__name__}",
                    "ok": False,
                    "action_ok": False,
                    "product_ok": False,
                }
        return {
            "id": record["id"],
            "user": record["user"],
            "expected": record["expected"]["intent"],
            "actual": actual_intent,
            "ok": ok,
            "action_ok": action_ok,
            "product_ok": product_ok,
        }

    results = await asyncio.gather(*(one(record) for record in records))
    total = len(results)
    passed = sum(1 for result in results if result["ok"])
    confusion = Counter(
        f"{result['expected']}->{result['actual']}" for result in results
    )
    action_total = sum(
        1 for record in records
        if record["expected"]["slots"].get("after_sale_action")
    )
    action_passed = sum(
        1
        for record, result in zip(records, results, strict=True)
        if record["expected"]["slots"].get("after_sale_action")
        and result.get("action_ok")
    )
    product_total = sum(
        1 for record in records
        if record["expected"]["slots"].get("product")
    )
    product_passed = sum(
        1
        for record, result in zip(records, results, strict=True)
        if record["expected"]["slots"].get("product")
        and result.get("product_ok")
    )
    failures = [result for result in results if not result["ok"]][:15]
    provider_errors = sum(
        1
        for result in results
        if str(result.get("actual", "")).startswith(
            "ERROR:ProviderAuthenticationError"
        )
    )
    return {
        "total": total,
        "passed": passed,
        "accuracy": round(passed / total, 3) if total else 0.0,
        "provider_errors": provider_errors,
        "slot_action_total": action_total,
        "slot_action_passed": action_passed,
        "slot_action_accuracy": round(action_passed / action_total, 3)
        if action_total
        else 0.0,
        "slot_product_total": product_total,
        "slot_product_passed": product_passed,
        "slot_product_accuracy": round(product_passed / product_total, 3)
        if product_total
        else 0.0,
        "confusion": dict(confusion),
        "failures": failures,
    }


def _tool_prompt(record: dict) -> list[dict]:
    turns = []
    for message in record["messages"]:
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        if content.startswith("工具返回:"):
            continue
        if content:
            turns.append(f"用户：{content}")
    return [
        {"role": "system", "content": TOOL_SYSTEM},
        {
            "role": "user",
            "content": "对话：\n" + "\n".join(turns) + "\n\n请输出动作序列。",
        },
    ]


async def eval_tool(container, records: list[dict], concurrency: int) -> dict:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(record: dict) -> dict:
        async with semaphore:
            try:
                raw = await asyncio.wait_for(
                    container.chat.complete(
                        _tool_prompt(record), model=container.settings.chat_model
                    ),
                    timeout=25,
                )
                actions = _parse_tool_actions(raw)
                if actions is None:
                    return {
                        "id": record["id"],
                        "expected": record["expected"]["actions"],
                        "actual": "INVALID",
                        "first_ok": False,
                        "set_ok": False,
                    }
                expected = record["expected"]["actions"]
                first_ok = bool(actions) and actions[0] == expected[0]
                set_ok = set(actions) == set(expected) and len(actions) == len(
                    expected
                )
                return {
                    "id": record["id"],
                    "expected": expected,
                    "actual": actions,
                    "first_ok": first_ok,
                    "set_ok": set_ok,
                }
            except Exception as exc:
                return {
                    "id": record["id"],
                    "expected": record["expected"]["actions"],
                    "actual": f"ERROR:{type(exc).__name__}",
                    "first_ok": False,
                    "set_ok": False,
                }

    results = await asyncio.gather(*(one(record) for record in records))
    error_records = [
        record for record in records if record["meta"]["error_codes"]
    ]
    error_results = [
        result for result in results
        if next(
            (
                record
                for record in error_records
                if record["id"] == result["id"]
            ),
            None,
        )
    ]
    # The safety sub-check uses one extra call per error record.
    async def error_safe(record: dict) -> bool:
        async with semaphore:
            code = record["meta"]["error_codes"][0]
            try:
                raw = await asyncio.wait_for(
                    container.chat.complete(
                        [
                            {"role": "system", "content": TOOL_ERROR_SYSTEM},
                            {
                                "role": "user",
                                "content": (
                                    f"用户诉求：{record['user']}\n"
                                    f"query_order_status 返回错误码：{code}\n"
                                    "下一步是否创建售后？"
                                ),
                            },
                        ],
                        model=container.settings.chat_model,
                    ),
                    timeout=20,
                )
                action = (_json_object(raw) or {}).get("action")
                return action == "none"
            except Exception:
                return False

    error_safe_results = await asyncio.gather(
        *(error_safe(record) for record in error_records)
    )
    total = len(results)
    first_passed = sum(1 for result in results if result["first_ok"])
    set_passed = sum(1 for result in results if result["set_ok"])
    failures = [
        result
        for result in results
        if not (result["first_ok"] and result["set_ok"])
    ][:15]
    provider_errors = sum(
        1
        for result in results
        if str(result.get("actual", "")).startswith(
            "ERROR:ProviderAuthenticationError"
        )
    )
    return {
        "total": total,
        "provider_errors": provider_errors,
        "first_action_passed": first_passed,
        "first_action_accuracy": round(first_passed / total, 3) if total else 0.0,
        "action_set_passed": set_passed,
        "action_set_accuracy": round(set_passed / total, 3) if total else 0.0,
        "error_total": len(error_records),
        "error_stop_passed": sum(1 for value in error_safe_results if value),
        "error_stop_rate": round(
            sum(1 for value in error_safe_results if value)
            / len(error_records),
            3,
        )
        if error_records
        else 0.0,
        "failures": failures,
    }


async def eval_sentinel(container, records: list[dict], concurrency: int) -> dict:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(record: dict) -> dict:
        async with semaphore:
            try:
                verdict = await asyncio.wait_for(
                    check_answer(
                        container,
                        question=record["question"],
                        answer=record["answer"],
                        intent=record["intent"],
                        sources=record.get("sources") or [],
                        tool_evidence={},
                        grounding_context=record.get("context", ""),
                    ),
                    timeout=20,
                )
                flagged = bool(verdict.flagged)
                expected = record["expected"] == "flag"
                ok = flagged == expected
                return {
                    "id": record["id"],
                    "case": record["case"],
                    "expected": record["expected"],
                    "flagged": flagged,
                    "checked": verdict.checked,
                    "ok": ok,
                    "reason": verdict.reason,
                    "error": verdict.error,
                }
            except Exception as exc:
                return {
                    "id": record["id"],
                    "case": record["case"],
                    "expected": record["expected"],
                    "flagged": False,
                    "checked": False,
                    "ok": False,
                    "reason": f"ERROR:{type(exc).__name__}",
                    "error": type(exc).__name__,
                }

    results = await asyncio.gather(*(one(record) for record in records))
    negatives = [r for r in results if r["case"] == "negative"]
    positives = [r for r in results if r["case"] == "positive"]
    provider_errors = sum(
        1
        for result in results
        if result.get("error") == "ProviderAuthenticationError"
    )
    return {
        "total": len(results),
        "provider_errors": provider_errors,
        "passed": sum(1 for result in results if result["ok"]),
        "accuracy": round(
            sum(1 for result in results if result["ok"]) / len(results), 3
        )
        if results
        else 0.0,
        "negative_total": len(negatives),
        "negative_blocked": sum(1 for result in negatives if result["flagged"]),
        "negative_block_rate": round(
            sum(1 for result in negatives if result["flagged"])
            / len(negatives),
            3,
        )
        if negatives
        else 0.0,
        "positive_total": len(positives),
        "positive_passed": sum(
            1 for result in positives if not result["flagged"]
        ),
        "positive_false_positive_rate": round(
            sum(1 for result in positives if result["flagged"])
            / len(positives),
            3,
        )
        if positives
        else 0.0,
        "not_checked": sum(
            1 for result in results if not result["checked"]
        ),
        "failures": [result for result in results if not result["ok"]][:15],
    }


def _write_markdown(path: Path, report: dict) -> None:
    lines = [
        "# 数据集评测基线",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 模型：{report['models']}",
        f"- 数据版本：{report['data_manifest']}",
        "",
        "## 意图识别",
        "",
        f"- 总数：{report['intent']['total']}，通过：{report['intent']['passed']}，准确率：{report['intent']['accuracy']}",
        f"- 售后动作槽位：{report['intent']['slot_action_passed']}/{report['intent']['slot_action_total']}",
        f"- 商品槽位：{report['intent']['slot_product_passed']}/{report['intent']['slot_product_total']}",
        "",
        "## 工具决策",
        "",
        f"- 首动作准确率：{report['tool']['first_action_accuracy']}（{report['tool']['first_action_passed']}/{report['tool']['total']}）",
        f"- 动作集合准确率：{report['tool']['action_set_accuracy']}（{report['tool']['action_set_passed']}/{report['tool']['total']}）",
        f"- 错误分支停止率：{report['tool']['error_stop_rate']}（{report['tool']['error_stop_passed']}/{report['tool']['error_total']}）",
        "",
        "## 回答哨兵",
        "",
        f"- 负样本拦截率：{report['sentinel']['negative_block_rate']}（{report['sentinel']['negative_blocked']}/{report['sentinel']['negative_total']}）",
        f"- 正样本误杀率：{report['sentinel']['positive_false_positive_rate']}（{report['sentinel']['positive_total'] - report['sentinel']['positive_passed']}/{report['sentinel']['positive_total']}）",
        f"- 未完成检查：{report['sentinel']['not_checked']}",
        "",
        "## 失败样例",
        "",
    ]
    for section, key in [
        ("意图", "intent"),
        ("工具", "tool"),
        ("哨兵", "sentinel"),
    ]:
        lines.append(f"### {section}")
        for failure in report[key]["failures"][:5]:
            lines.append(
                f"- `{failure.get('id')}` 期望 `{failure.get('expected')}`，实际 `{failure.get('actual', failure.get('flagged'))}`：{failure.get('user', failure.get('reason', ''))}"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="limit each suite")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", default="artifacts/eval-dataset")
    args = parser.parse_args()

    manifest_path = DATA_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    intents = _read_jsonl(DATA_ROOT / "ecom_intent_300.jsonl")
    tools = _read_jsonl(DATA_ROOT / "ecom_tool_200.jsonl")
    sentinels = _read_jsonl(DATA_ROOT / "sentinel_100.jsonl")
    if args.limit:
        intents = intents[: args.limit]
        tools = tools[: args.limit]
        sentinels = sentinels[: args.limit]

    settings = get_settings()
    container = build_container(settings)
    print(
        f"Evaluating intent={len(intents)} tool={len(tools)} sentinel={len(sentinels)} "
        f"model={settings.router_model}",
        file=sys.stderr,
    )
    intent_report = await eval_intent(container, intents, args.concurrency)
    tool_report = await eval_tool(container, tools, args.concurrency)
    sentinel_report = await eval_sentinel(
        container, sentinels, args.concurrency
    )
    provider_errors = (
        intent_report.get("provider_errors", 0)
        + tool_report.get("provider_errors", 0)
        + sentinel_report.get("provider_errors", 0)
    )
    attempted = len(intents) + len(tools) + len(sentinels)
    if provider_errors and provider_errors >= attempted * 0.5:
        print(
            "Provider authentication failed for most calls; refusing to "
            "overwrite the previous report. Check CHAT_API_KEY / free quota.",
            file=sys.stderr,
        )
        return 2
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models": {
            "router": settings.router_model,
            "chat": settings.chat_model,
            "sentinel": settings.sentinel_model_name,
        },
        "data_manifest": {
            "seed": manifest.get("seed"),
            "counts": manifest.get("counts"),
        },
        "intent": intent_report,
        "tool": tool_report,
        "sentinel": sentinel_report,
        "limits": args.limit or None,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_markdown(output.with_suffix(".md"), report)
    print(json.dumps(
        {
            "intent_accuracy": report["intent"]["accuracy"],
            "tool_first_action": report["tool"]["first_action_accuracy"],
            "tool_action_set": report["tool"]["action_set_accuracy"],
            "sentinel_negative_block": report["sentinel"]["negative_block_rate"],
            "sentinel_false_positive": report["sentinel"]["positive_false_positive_rate"],
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
