"""Safety contracts for the scripted evaluation run."""

from __future__ import annotations

import pytest

from scripts.evaluate import EvaluationRun, assert_evaluation_database


def test_evaluation_database_requires_explicit_test_or_eval_marker():
    with pytest.raises(RuntimeError, match="test/eval"):
        assert_evaluation_database(
            "postgresql+psycopg://cs_user:secret@localhost:5432/cs_db"
        )

    assert_evaluation_database(
        "postgresql+psycopg://cs_user:secret@localhost:5432/cs_test"
    )
    assert_evaluation_database(
        "postgresql+psycopg://cs_user:secret@localhost:5432/cs_eval"
    )


@pytest.mark.anyio
async def test_evaluation_run_cleans_recorded_conversations_on_success():
    deleted: list[int] = []

    async def delete_conversation(conversation_id: int) -> None:
        deleted.append(conversation_id)

    run = EvaluationRun(delete_conversation)
    run.record_conversation(101)
    run.record_conversation(102)
    await run.cleanup()

    assert deleted == [101, 102]


@pytest.mark.anyio
async def test_evaluation_run_cleans_recorded_conversations_after_failure():
    deleted: list[int] = []

    async def delete_conversation(conversation_id: int) -> None:
        deleted.append(conversation_id)

    run = EvaluationRun(delete_conversation)
    with pytest.raises(RuntimeError, match="scenario failed"):
        try:
            run.record_conversation(203)
            raise RuntimeError("scenario failed")
        finally:
            await run.cleanup()

    assert deleted == [203]
