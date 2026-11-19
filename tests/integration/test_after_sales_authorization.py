"""After-sales facts are scoped through owned orders, never a user_id column."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.helpers import create_after_sale, create_order, create_user


@pytest.mark.anyio
async def test_owner_receives_after_sales_facts_in_chronological_order(db_session):
    from app.db.repositories.after_sales import lookup_after_sales

    owner = await create_user(db_session, "after-sale-owner")
    order = await create_order(db_session, owner.id, "ORD-AFTER-1")
    await create_after_sale(
        db_session,
        order.id,
        service_type="退货退款",
        status="已完成",
        refund_amount="88.50",
        reason="商品破损",
    )
    await create_after_sale(
        db_session,
        order.id,
        service_type="仅退款",
        status="处理中",
        refund_amount="20.00",
        reason="少件",
    )

    facts = await lookup_after_sales(db_session, owner.id, "ORD-AFTER-1")

    assert facts is not None
    assert [fact.reason for fact in facts] == ["商品破损", "少件"]
    assert facts[0].service_type == "退货退款"
    assert facts[0].status == "已完成"
    assert facts[0].refund_amount == Decimal("88.50")
    assert set(facts[0].model_dump()) == {
        "id",
        "service_type",
        "status",
        "refund_amount",
        "reason",
        "created_at",
        "updated_at",
    }


@pytest.mark.anyio
async def test_owned_order_without_after_sales_returns_empty_list(db_session):
    from app.db.repositories.after_sales import lookup_after_sales

    owner = await create_user(db_session, "empty-after-sale-owner")
    await create_order(db_session, owner.id, "ORD-AFTER-EMPTY")

    facts = await lookup_after_sales(db_session, owner.id, "ORD-AFTER-EMPTY")

    assert facts == []


@pytest.mark.anyio
async def test_missing_order_returns_none(db_session):
    from app.db.repositories.after_sales import lookup_after_sales

    user = await create_user(db_session, "missing-after-sale-owner")

    facts = await lookup_after_sales(db_session, user.id, "ORD-NOT-FOUND")

    assert facts is None


@pytest.mark.anyio
async def test_other_users_order_returns_same_none_as_missing(db_session):
    from app.db.repositories.after_sales import lookup_after_sales

    owner = await create_user(db_session, "private-after-sale-owner")
    other = await create_user(db_session, "other-after-sale-user")
    order = await create_order(db_session, owner.id, "ORD-AFTER-PRIVATE")
    await create_after_sale(
        db_session,
        order.id,
        service_type="仅退款",
        status="已完成",
        refund_amount="899.00",
        reason="隐私记录",
    )

    unauthorized = await lookup_after_sales(
        db_session, other.id, "ORD-AFTER-PRIVATE"
    )
    missing = await lookup_after_sales(db_session, other.id, "ORD-NOT-FOUND")

    assert unauthorized is None
    assert missing is None
    assert unauthorized == missing


@pytest.mark.anyio
async def test_after_sale_tool_only_forwards_authenticated_user_id(
    db_session, monkeypatch
):
    from app.tools import after_sales as after_sales_tool

    calls = []

    async def fake_lookup(session, user_id, order_no):
        calls.append((session, user_id, order_no))
        return []

    monkeypatch.setattr(
        after_sales_tool.after_sales_repo,
        "lookup_after_sales",
        fake_lookup,
    )

    result = await after_sales_tool.lookup_after_sales(
        db_session, 42, "ORD-AUTHENTICATED"
    )

    assert result == []
    assert calls == [(db_session, 42, "ORD-AUTHENTICATED")]
