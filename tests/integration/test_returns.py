"""Integration tests for photo return/exchange intake."""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from app.api.deps import get_container
from app.core.config import get_settings
from app.db.models import (
    ChatMessage,
    Conversation,
    HandoffTicket,
    Order,
    OrderItem,
    Product,
    ReturnAnalysis,
)
from app.db.session import create_session_factory
from app.graph.builder import Container
from app.main import create_app
from app.services.vision import VisionResult
from tests.helpers import create_order, create_user


class FakeVision:
    def __init__(self, passed: bool = True):
        self.passed = passed
        self.calls = []

    async def analyze(self, image_path):
        self.calls.append(image_path)
        if not self.passed:
            return VisionResult(
                passed=False,
                confidence=0.1,
                message="识别置信度不足，请重拍清晰照片后再提交",
                provider="fake",
            )
        return VisionResult(
            passed=True,
            confidence=0.82,
            product="手机",
            product_class="cell phone",
            model="",
            color="",
            accessories=[],
            damage_type="未知",
            detected_objects=[
                {
                    "label": "cell phone",
                    "label_zh": "手机",
                    "confidence": 0.82,
                    "box": [0, 0, 100, 100],
                }
            ],
            message="已识别到商品主体",
            provider="fake",
        )


def _make_container(vision: FakeVision) -> Container:
    return Container(
        chat=None,
        providers=None,
        settings=get_settings(),
        vision=vision,
    )


async def _build_client(container: Container) -> AsyncClient:
    app = create_app(
        settings=get_settings(),
        session_factory=create_session_factory(get_settings().database_url),
    )
    app.dependency_overrides[get_container] = lambda: container
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _login(client: AsyncClient, username: str) -> str:
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "password123"},
    )
    return response.json()["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _photo_bytes() -> bytes:
    image = Image.new("RGB", (320, 320), "white")
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


async def _seed_ticket(
    db_session,
    user_id: int,
    conversation_id: int,
    summary: str,
) -> HandoffTicket:
    ticket = HandoffTicket(
        conversation_id=conversation_id,
        user_id=user_id,
        status="open",
        kind="human",
        category="general",
        summary=summary,
        slots={},
        recent_messages=[],
        evidence={},
    )
    db_session.add(ticket)
    await db_session.flush()
    await db_session.refresh(ticket)
    return ticket


@pytest.mark.anyio
async def test_return_flow_creates_ticket_bypasses_limit_and_reports_answer(
    db_session,
):
    user = await create_user(db_session, "return-flow-user")
    admin = await create_user(db_session, "return-flow-admin", role="admin")
    order = await create_order(
        db_session,
        user.id,
        "ORD-RETURN-001",
        status="已完成",
        total="999.00",
        items=[("智能手机", 1, "999.00")],
    )
    product = (
        await db_session.execute(
            select(Product).where(Product.name == "智能手机")
        )
    ).scalar_one()
    product.model = "Mate 90"
    product.color = "曜石黑"
    item = (
        await db_session.execute(
            select(OrderItem).where(OrderItem.order_id == order.id)
        )
    ).scalar_one()
    item.model = "Mate 90"
    item.color = "曜石黑"
    await db_session.commit()

    # Two unresolved tickets in other conversations must not block the return.
    other_a = Conversation(user_id=user.id, title="其他工单 A")
    other_b = Conversation(user_id=user.id, title="其他工单 B")
    db_session.add_all([other_a, other_b])
    await db_session.flush()
    await _seed_ticket(db_session, user.id, other_a.id, "订单一直没发货")
    await _seed_ticket(db_session, user.id, other_b.id, "怎么申请退款")
    await db_session.commit()

    vision = FakeVision()
    container = _make_container(vision)
    async with await _build_client(container) as client:
        token = await _login(client, "return-flow-user")
        conversation = (
            await client.post(
                "/api/conversations", json={}, headers=_auth(token)
            )
        ).json()
        analyzed = await client.post(
            "/api/returns/analyze",
            data={"conversation_id": str(conversation["id"])},
            files={"file": ("phone.jpg", _photo_bytes(), "image/jpeg")},
            headers=_auth(token),
        )
        assert analyzed.status_code == 200, analyzed.text
        analysis = analyzed.json()
        assert analysis["passed"] is True
        assert analysis["product_class"] == "cell phone"

        context = await client.get(
            "/api/returns/orders/ORD-RETURN-001", headers=_auth(token)
        )
        assert context.status_code == 200
        order_item = context.json()["items"][0]
        assert order_item["model"] == "Mate 90"
        assert order_item["color"] == "曜石黑"

        submitted = await client.post(
            "/api/returns",
            json={
                "conversation_id": conversation["id"],
                "analysis_id": analysis["analysis_id"],
                "order_no": "ORD-RETURN-001",
                "order_item_id": order_item["order_item_id"],
                "damage_level": "严重",
                "damage_type": "屏幕裂",
                "reason": "运输破损",
                "accessories": ["主机", "包装盒"],
                "return_address": "上海市浦东新区测试路 1 号",
                "phone": "13800138000",
                "confirmed": True,
            },
            headers=_auth(token),
        )
        assert submitted.status_code == 200, submitted.text
        result = submitted.json()
        assert result["answer"] == "已联系管理员，请稍候。"
        assert result["duplicate"] is False

        messages = (
            await client.get(
                f"/api/conversations/{conversation['id']}/messages",
                headers=_auth(token),
            )
        ).json()
        assert messages[-1]["content"] == "已联系管理员，请稍候。"
        assert messages[-1]["intent"] == "handoff"

        admin_token = await _login(client, "return-flow-admin")
        image = await client.get(
            f"/api/admin/handoffs/{result['ticket_id']}/return-image",
            headers=_auth(admin_token),
        )
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/jpeg"

        # A second application for the same order/SKU/damage/reason should
        # reuse the existing return ticket rather than creating a duplicate.
        second_conversation = (
            await client.post(
                "/api/conversations", json={}, headers=_auth(token)
            )
        ).json()
        second_analysis = (
            await client.post(
                "/api/returns/analyze",
                data={"conversation_id": str(second_conversation["id"])},
                files={"file": ("phone.jpg", _photo_bytes(), "image/jpeg")},
                headers=_auth(token),
            )
        ).json()
        duplicate = await client.post(
            "/api/returns",
            json={
                "conversation_id": second_conversation["id"],
                "analysis_id": second_analysis["analysis_id"],
                "order_no": "ORD-RETURN-001",
                "order_item_id": order_item["order_item_id"],
                "damage_level": "严重",
                "damage_type": "屏幕裂",
                "reason": "运输破损",
                "accessories": ["主机", "包装盒"],
                "return_address": "上海市浦东新区测试路 1 号",
                "phone": "13800138000",
                "confirmed": True,
            },
            headers=_auth(token),
        )
        assert duplicate.status_code == 200, duplicate.text
        duplicate_body = duplicate.json()
        assert duplicate_body["duplicate"] is True
        assert duplicate_body["ticket_id"] == result["ticket_id"]
        assert "已在处理中" in duplicate_body["answer"]

    ticket = await db_session.get(HandoffTicket, result["ticket_id"])
    assert ticket is not None
    assert ticket.kind == "human"
    assert ticket.category == "return_exchange"
    assert ticket.status == "open"
    assert ticket.evidence["return_request"]["order_no"] == "ORD-RETURN-001"
    assert ticket.evidence["return_request"]["damage_type"] == "屏幕裂"

    analysis_row = await db_session.get(ReturnAnalysis, analysis["analysis_id"])
    assert analysis_row is not None
    assert analysis_row.consumed_at is not None

    count = (
        await db_session.execute(
            select(HandoffTicket).where(HandoffTicket.user_id == user.id)
        )
    ).scalars().all()
    assert len(count) == 3


@pytest.mark.anyio
async def test_low_confidence_photo_cannot_create_ticket(db_session):
    user = await create_user(db_session, "return-low-confidence-user")
    await create_order(
        db_session,
        user.id,
        "ORD-RETURN-LOW",
        status="已完成",
        total="999.00",
        items=[("智能手机", 1, "999.00")],
    )
    vision = FakeVision(passed=False)
    container = _make_container(vision)
    async with await _build_client(container) as client:
        token = await _login(client, "return-low-confidence-user")
        conversation = (
            await client.post(
                "/api/conversations", json={}, headers=_auth(token)
            )
        ).json()
        analyzed = await client.post(
            "/api/returns/analyze",
            data={"conversation_id": str(conversation["id"])},
            files={"file": ("phone.jpg", _photo_bytes(), "image/jpeg")},
            headers=_auth(token),
        )
        assert analyzed.status_code == 200
        analysis = analyzed.json()
        assert analysis["passed"] is False

        submitted = await client.post(
            "/api/returns",
            json={
                "conversation_id": conversation["id"],
                "analysis_id": analysis["analysis_id"],
                "order_no": "ORD-RETURN-LOW",
                "order_item_id": 1,
                "damage_level": "严重",
                "damage_type": "屏幕裂",
                "reason": "运输破损",
                "accessories": [],
                "return_address": "上海市浦东新区测试路 1 号",
                "phone": "13800138000",
                "confirmed": True,
            },
            headers=_auth(token),
        )
        assert submitted.status_code == 422
        assert "识别" in submitted.json()["detail"]

    tickets = (
        await db_session.execute(
            select(HandoffTicket).where(HandoffTicket.user_id == user.id)
        )
    ).scalars().all()
    assert tickets == []


@pytest.mark.anyio
async def test_photo_class_mismatch_is_rejected_at_submit(db_session):
    user = await create_user(db_session, "return-mismatch-user")
    order = await create_order(
        db_session,
        user.id,
        "ORD-RETURN-MISMATCH",
        status="已完成",
        total="999.00",
        items=[("机械键盘", 1, "999.00")],
    )
    product = (
        await db_session.execute(
            select(Product).where(Product.name == "机械键盘")
        )
    ).scalar_one()
    product.model = "87键"
    product.color = "深空灰"
    await db_session.commit()

    vision = FakeVision()
    container = _make_container(vision)
    async with await _build_client(container) as client:
        token = await _login(client, "return-mismatch-user")
        conversation = (
            await client.post(
                "/api/conversations", json={}, headers=_auth(token)
            )
        ).json()
        analysis = (
            await client.post(
                "/api/returns/analyze",
                data={"conversation_id": str(conversation["id"])},
                files={"file": ("phone.jpg", _photo_bytes(), "image/jpeg")},
                headers=_auth(token),
            )
        ).json()
        context = (
            await client.get(
                "/api/returns/orders/ORD-RETURN-MISMATCH",
                headers=_auth(token),
            )
        ).json()
        submitted = await client.post(
            "/api/returns",
            json={
                "conversation_id": conversation["id"],
                "analysis_id": analysis["analysis_id"],
                "order_no": "ORD-RETURN-MISMATCH",
                "order_item_id": context["items"][0]["order_item_id"],
                "damage_level": "中等",
                "damage_type": "外壳磕碰",
                "reason": "质量问题",
                "accessories": [],
                "return_address": "上海市浦东新区测试路 1 号",
                "phone": "13800138000",
                "confirmed": True,
            },
            headers=_auth(token),
        )
        assert submitted.status_code == 422
        assert "不一致" in submitted.json()["detail"]
