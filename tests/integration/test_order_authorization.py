"""Order ownership and authorization integration tests."""

from __future__ import annotations

import pytest

from app.schemas.orders import OrderFact
from app.tools.orders import get_order_and_logistics, list_owned_orders
from tests.helpers import create_order, create_user


@pytest.mark.anyio
async def test_owner_receives_order_with_logistics(db_session):
    owner = await create_user(db_session, "owner-a")
    await create_order(
        db_session,
        owner.id,
        "ORD-1001",
        status="运输中",
        items=[("声动 Pro 耳机", 1, "299.00")],
        logistics=[
            {"status": "已揽收", "location": "上海仓", "description": "已揽收"},
            {"status": "运输中", "location": "杭州转运中心", "description": "运输中"},
        ],
    )
    fact = await get_order_and_logistics(db_session, owner.id, "ORD-1001")
    assert isinstance(fact, OrderFact)
    assert fact.status == "运输中"
    assert len(fact.items) == 1
    assert len(fact.logistics) == 2


@pytest.mark.anyio
async def test_other_user_gets_same_result_as_missing(db_session):
    owner = await create_user(db_session, "owner-a")
    other = await create_user(db_session, "owner-b")
    await create_order(db_session, owner.id, "ORD-1001")

    other_result = await get_order_and_logistics(db_session, other.id, "ORD-1001")
    missing_result = await get_order_and_logistics(db_session, other.id, "DOES-NOT-EXIST")
    assert other_result is None
    assert missing_result is None


@pytest.mark.anyio
async def test_order_list_only_contains_owned_orders(db_session):
    owner = await create_user(db_session, "order-list-owner")
    other = await create_user(db_session, "order-list-other")
    await create_order(db_session, owner.id, "ORD-LIST-1", status="运输中")
    await create_order(db_session, owner.id, "ORD-LIST-2", status="已完成")
    await create_order(db_session, other.id, "ORD-LIST-3", status="已完成")

    facts = await list_owned_orders(db_session, owner.id)

    assert [fact.order_no for fact in facts] == ["ORD-LIST-2", "ORD-LIST-1"]


@pytest.mark.anyio
async def test_order_repository_requires_user_id_argument(db_session):
    """The lookup signature must always receive the authenticated user id."""
    import inspect

    from app.db.repositories.orders import get_order_and_logistics as repo_fn

    params = list(inspect.signature(repo_fn).parameters)
    assert "user_id" in params
    assert "order_no" in params
