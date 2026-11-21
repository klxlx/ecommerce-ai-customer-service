"""Photo return/exchange intake: validate, analyze, and create a human ticket."""

from __future__ import annotations

import asyncio
import io
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import (
    Order,
    OrderItem,
    Product,
    ReturnAnalysis,
    User,
)
from app.db.repositories import chat as chat_repo
from app.schemas.returns import (
    ReturnAnalysisOut,
    ReturnOrderContextOut,
    ReturnOrderItemOut,
    ReturnSubmitIn,
    ReturnSubmitOut,
)
from app.services import handoff as handoff_service
from app.services.vision import (
    VisionResult,
    expected_vision_classes,
)

MAX_IMAGE_PIXELS = 40_000_000
RETURN_ANSWER = "已联系管理员，请稍候。"
RETURN_DUPLICATE_ANSWER = "该退换货申请已在处理中，已联系管理员，请稍候。"
_RESTRICTED_RETURN_STATUSES = {"待发货", "运输中"}


class ReturnError(Exception):
    """Base class for customer-facing return errors."""


class ReturnValidationError(ReturnError):
    """The photo or form does not satisfy the return intake rules."""


class ReturnConflict(ReturnError):
    """The conversation already has an active human ticket."""


def _image_dir(settings: Settings) -> Path:
    root = Path(__file__).resolve().parents[2]
    configured = Path(settings.return_image_dir)
    return configured if configured.is_absolute() else root / configured


def _normalize_image(data: bytes, max_bytes: int) -> bytes:
    if len(data) > max_bytes:
        raise ReturnValidationError(
            f"照片不能超过 {max_bytes // (1024 * 1024)} MB"
        )
    if not data:
        raise ReturnValidationError("请上传商品照片")
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert("RGB")
            if image.width < 240 or image.height < 240:
                raise ReturnValidationError("照片分辨率过低，请重新拍摄")
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=85, optimize=True)
            return output.getvalue()
    except ReturnValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError):
        raise ReturnValidationError("照片格式无法识别，请上传 JPG 或 PNG 图片")
    except Image.DecompressionBombError:
        raise ReturnValidationError("照片尺寸过大，请压缩后重试")


def _ensure_image_dir(settings: Settings) -> Path:
    directory = _image_dir(settings)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


async def analyze_photo(
    session: AsyncSession,
    user: User,
    conversation_id: int,
    data: bytes,
    container,
) -> ReturnAnalysisOut:
    conversation = await chat_repo.get_conversation_owned(
        session, conversation_id, user.id
    )
    if conversation is None:
        raise ReturnValidationError("会话不存在")
    active = await handoff_service.get_active_ticket(session, conversation_id)
    if active is not None:
        raise ReturnConflict("当前会话已转人工，请直接在会话中联系客服")

    settings = container.settings
    normalized = await asyncio.to_thread(
        _normalize_image,
        data,
        max(1, settings.return_image_max_mb) * 1024 * 1024,
    )
    image_id = uuid.uuid4().hex
    image_path = _ensure_image_dir(settings) / f"{image_id}.jpg"
    await asyncio.to_thread(image_path.write_bytes, normalized)

    analyzer = getattr(container, "vision", None)
    if analyzer is None:
        result = VisionResult(
            passed=False,
            confidence=0.0,
            message="视觉模型未配置，请联系管理员补充 YOLO 模型",
            provider="unavailable",
        )
    else:
        result = await analyzer.analyze(image_path)

    analysis = ReturnAnalysis(
        user_id=user.id,
        conversation_id=conversation_id,
        image_id=image_id,
        image_path=str(image_path),
        result=asdict(result),
        confidence=float(result.confidence or 0),
        passed=bool(result.passed),
        failure_reason="" if result.passed else result.message,
    )
    session.add(analysis)
    await session.flush()

    return ReturnAnalysisOut(
        analysis_id=analysis.id,
        image_id=image_id,
        passed=analysis.passed,
        confidence=analysis.confidence,
        product=result.product,
        product_class=result.product_class,
        model=result.model,
        color=result.color,
        accessories=result.accessories,
        damage_type=result.damage_type,  # type: ignore[arg-type]
        detected_objects=result.detected_objects,  # type: ignore[arg-type]
        message=result.message,
        provider=result.provider,
    )


async def get_order_context(
    session: AsyncSession,
    user_id: int,
    order_no: str,
) -> ReturnOrderContextOut:
    order = (
        await session.execute(
            select(Order).where(
                Order.order_no == order_no,
                Order.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if order is None:
        raise ReturnValidationError("未查询到该订单，请核对订单号")
    items = list(
        (
            await session.execute(
                select(OrderItem)
                .where(OrderItem.order_id == order.id)
                .order_by(OrderItem.id)
            )
        )
        .scalars()
        .all()
    )
    if not items:
        raise ReturnValidationError("该订单没有可退换的商品")
    product_ids = [item.product_id for item in items]
    products = {
        product.id: product
        for product in (
            await session.execute(
                select(Product).where(Product.id.in_(product_ids))
            )
        )
        .scalars()
        .all()
    }
    return ReturnOrderContextOut(
        order_no=order.order_no,
        status=order.status,
        items=[
            ReturnOrderItemOut(
                order_item_id=item.id,
                sku=products[item.product_id].sku
                if item.product_id in products
                else "",
                product_name=item.product_name,
                model=item.model or (
                    products[item.product_id].model
                    if item.product_id in products
                    else ""
                ),
                color=item.color or (
                    products[item.product_id].color
                    if item.product_id in products
                    else ""
                ),
                quantity=item.quantity,
                unit_price=item.unit_price,
            )
            for item in items
        ],
    )


async def submit_request(
    session: AsyncSession,
    user: User,
    payload: ReturnSubmitIn,
) -> ReturnSubmitOut:
    if not payload.confirmed:
        raise ReturnValidationError("请先确认照片和商品情况属实")
    if payload.damage_type == "未知":
        raise ReturnValidationError("请选择破损类型")

    conversation = await chat_repo.get_conversation_owned(
        session, payload.conversation_id, user.id
    )
    if conversation is None:
        raise ReturnValidationError("会话不存在")
    active = await handoff_service.get_active_ticket(
        session, payload.conversation_id
    )
    if active is not None:
        raise ReturnConflict("当前会话已转人工，请直接在会话中联系客服")

    analysis = (
        await session.execute(
            select(ReturnAnalysis).where(
                ReturnAnalysis.id == payload.analysis_id,
                ReturnAnalysis.user_id == user.id,
                ReturnAnalysis.conversation_id == payload.conversation_id,
            )
        )
    ).scalar_one_or_none()
    if analysis is None:
        raise ReturnValidationError("照片识别记录不存在，请重新上传")
    if analysis.consumed_at is not None:
        raise ReturnValidationError("该照片已提交过，请重新上传")
    if not analysis.passed:
        raise ReturnValidationError(
            analysis.failure_reason or "照片未通过识别，请重新拍摄"
        )

    order = (
        await session.execute(
            select(Order).where(
                Order.order_no == payload.order_no,
                Order.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if order is None:
        raise ReturnValidationError("未查询到该订单，请核对订单号")
    if order.status in _RESTRICTED_RETURN_STATUSES:
        raise ReturnValidationError("订单尚未签收，暂不能提交退换货申请")

    item = (
        await session.execute(
            select(OrderItem).where(
                OrderItem.id == payload.order_item_id,
                OrderItem.order_id == order.id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise ReturnValidationError("订单商品不存在，请重新选择")
    product = await session.get(Product, item.product_id)
    if product is None:
        raise ReturnValidationError("商品信息不存在，请联系人工客服")

    result = analysis.result or {}
    detected_labels = {
        str(obj.get("label") or "")
        for obj in result.get("detected_objects", [])
        if isinstance(obj, dict)
    }
    expected = expected_vision_classes(product.name)
    if expected and not (expected & detected_labels):
        raise ReturnValidationError("照片中的商品与订单商品不一致，请重新拍摄")

    dedupe_key = (
        f"{order.order_no}|{product.sku}|{payload.damage_type}|{payload.reason}"
    )
    summary = (
        f"退换货：{product.name}（{product.sku}）"
        f"{payload.damage_type} / {payload.damage_level}"
    )
    slots = {
        "order_no": order.order_no,
        "sku": product.sku,
        "product_name": product.name,
        "model": item.model or product.model,
        "color": item.color or product.color,
        "damage_type": payload.damage_type,
        "damage_level": payload.damage_level,
        "reason": payload.reason,
        "accessories": payload.accessories,
        "return_address": payload.return_address,
        "phone": payload.phone,
    }
    evidence = {
        "return_request": {
            "dedupe_key": dedupe_key,
            "order_no": order.order_no,
            "order_item_id": item.id,
            "sku": product.sku,
            "product_name": product.name,
            "model": item.model or product.model,
            "color": item.color or product.color,
            "damage_type": payload.damage_type,
            "damage_level": payload.damage_level,
            "reason": payload.reason,
            "accessories": payload.accessories,
            "return_address": payload.return_address,
            "phone": payload.phone,
            "image_id": analysis.image_id,
            "analysis_id": analysis.id,
            "confidence": analysis.confidence,
            "detected_objects": result.get("detected_objects", []),
        },
        "tool": {
            "order": {
                "order_no": order.order_no,
                "status": order.status,
                "total_amount": str(order.total_amount),
            }
        },
    }

    recent = await chat_repo.list_messages(session, payload.conversation_id)
    recent_messages = [
        {"role": message.role, "content": message.content}
        for message in recent[-6:]
    ]
    ticket_result = await handoff_service.create_return_ticket(
        session,
        conversation_id=payload.conversation_id,
        user_id=user.id,
        summary=summary,
        slots=slots,
        recent_messages=recent_messages,
        evidence=evidence,
        dedupe_key=dedupe_key,
    )
    analysis.consumed_at = datetime.now(timezone.utc)

    answer = RETURN_DUPLICATE_ANSWER if ticket_result.duplicate else RETURN_ANSWER
    if not ticket_result.duplicate:
        await handoff_service.add_status_notice(
            session,
            ticket_result.ticket,
            "退换货申请已提交，客服会尽快处理。",
        )
    assistant_message = await chat_repo.add_message(
        session,
        payload.conversation_id,
        "assistant",
        answer,
        intent="handoff",
    )
    if conversation.title == chat_service_default_title():
        conversation.title = summary[:20]
    await session.commit()

    return ReturnSubmitOut(
        ticket_id=ticket_result.ticket.id,
        duplicate=ticket_result.duplicate,
        answer=answer,
        conversation_id=payload.conversation_id,
        message_id=assistant_message.id,
    )


def chat_service_default_title() -> str:
    # Imported lazily to avoid a circular import at module load.
    from app.services.chat import DEFAULT_CONVERSATION_TITLE

    return DEFAULT_CONVERSATION_TITLE


async def ticket_image_path(
    session: AsyncSession,
    handoff_id: int,
    settings: Settings,
) -> Path | None:
    ticket = await handoff_service.get_ticket(session, handoff_id)
    if ticket is None or ticket.category != "return_exchange":
        return None
    return_request = (ticket.evidence or {}).get("return_request") or {}
    image_id = str(return_request.get("image_id") or "")
    if not image_id:
        return None
    analysis = (
        await session.execute(
            select(ReturnAnalysis).where(
                ReturnAnalysis.image_id == image_id
            )
        )
    ).scalar_one_or_none()
    if analysis is None:
        return None
    path = Path(analysis.image_path).resolve()
    allowed_dir = _image_dir(settings).resolve()
    if allowed_dir not in path.parents or not path.is_file():
        return None
    return path
