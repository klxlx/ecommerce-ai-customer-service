"""Scripted evaluation of business branches and security cases.

Runs the customer-service orchestration against a seeded database using mock
providers (no external API keys) and prints a pass/fail report.

Usage:
    MOCK_PROVIDERS=1 python -m scripts.evaluate
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.bootstrap import build_container
from app.core.config import get_settings
from app.db.models import Conversation, User
from app.db.repositories import chat as chat_repo
from app.db.session import create_session_factory
from app.rag.indexer import index_registered_pdfs
from app.services import chat as chat_service
from scripts import seed_demo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PDF_REGISTRY_PATH = PROJECT_ROOT / "data" / "knowledge" / "pdf_sources.json"


def assert_evaluation_database(database_url: str) -> None:
    """Reject evaluation runs that point at an unmarked runtime database."""
    database_name = (make_url(database_url).database or "").lower()
    parts = database_name.replace("-", "_").split("_")
    if not {"test", "eval", "e2e"}.intersection(parts):
        raise RuntimeError(
            "Refusing to seed or modify a database without an explicit "
            "test/eval/e2e marker in its name"
        )


class EvaluationRun:
    """Track evaluation-created conversations and remove only those records."""

    def __init__(
        self,
        delete_conversation: Callable[[int], Awaitable[None]],
    ) -> None:
        self._delete_conversation = delete_conversation
        self._conversation_ids: list[int] = []
        self._cleaned_ids: set[int] = set()

    @property
    def conversation_ids(self) -> tuple[int, ...]:
        return tuple(self._conversation_ids)

    def record_conversation(self, conversation_id: int) -> None:
        if conversation_id not in self._conversation_ids:
            self._conversation_ids.append(conversation_id)

    async def cleanup(self) -> None:
        failures: list[str] = []
        for conversation_id in self._conversation_ids:
            if conversation_id in self._cleaned_ids:
                continue
            try:
                await self._delete_conversation(conversation_id)
            except Exception as exc:
                failures.append(f"{conversation_id}: {exc}")
            else:
                self._cleaned_ids.add(conversation_id)
        if failures:
            raise RuntimeError(
                "Evaluation conversation cleanup failed: " + "; ".join(failures)
            )


async def _run_one(
    session,
    container,
    user,
    message: str,
    expect: str,
    run: EvaluationRun,
) -> tuple[bool, str | None]:
    conv = await chat_repo.create_conversation(session, user.id)
    await session.commit()
    run.record_conversation(conv.id)
    result = await chat_service.run_chat(session, user, container, conv.id, message)
    ok = result is not None and expect in (result.answer or "")
    return ok, (result.answer if result else None)


def _build_conversation_cleaner(session_factory):
    async def delete_conversation(conversation_id: int) -> None:
        async with session_factory() as cleanup_session:
            conversation = await cleanup_session.get(Conversation, conversation_id)
            if conversation is not None and conversation.deleted_at is None:
                conversation.deleted_at = datetime.now(timezone.utc)
                await cleanup_session.commit()

    return delete_conversation


async def main() -> int:
    settings = get_settings()
    assert_evaluation_database(settings.database_url)
    container = build_container(settings)
    factory = create_session_factory(settings.database_url)
    run = EvaluationRun(_build_conversation_cleaner(factory))

    cleanup_failed = False
    try:
        async with factory() as session:
            await seed_demo._seed_users(session)
            products = await seed_demo._seed_products(session)
            customer = (
                await session.execute(select(User).where(User.username == "customer"))
            ).scalar_one()
            await seed_demo._seed_orders(session, customer, products)
            index_result = await index_registered_pdfs(
                session,
                container.providers.embeddings,
                PDF_REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                embedding_model=settings.embedding_model,
            )
            if index_result.errors:
                for error in index_result.errors:
                    print(f"INDEX ERROR {error}", file=sys.stderr)
                return 1

            scenarios = [
                ("greeting", customer, "你好", "购物客服"),
                ("order_query", customer, "查询订单 ORD-20261117-0001 的物流", "运输中"),
                ("handoff", customer, "转人工", "人工"),
                ("policy_knowledge", customer, "退换货政策是什么", "资料"),
                ("chit_chat_guidance", customer, "今天天气不错", "很高兴"),
            ]

            passed = 0
            for name, user, message, expect in scenarios:
                ok, answer = await _run_one(
                    session,
                    container,
                    user,
                    message,
                    expect,
                    run,
                )
                status = "PASS" if ok else "FAIL"
                print(f"[{status}] {name}: {message!r} -> {answer!r}")
                passed += int(ok)
    finally:
        try:
            await run.cleanup()
            print(
                f"Cleaned {len(run.conversation_ids)} evaluation conversations.",
                file=sys.stderr,
            )
        except Exception as exc:
            cleanup_failed = True
            print(f"CLEANUP ERROR {exc}", file=sys.stderr)

    print(f"\n{passed}/{len(scenarios)} scenarios passed")
    return 0 if passed == len(scenarios) and not cleanup_failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
