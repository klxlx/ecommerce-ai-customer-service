"""Deterministic formatters for slots and tool evidence.

Rules:
- Never serialize a raw JSON object into a prompt.
- Only expose allowlisted business fields.
- Return metadata so callers can record what was included or ignored.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.context.budget import truncate_text

_SLOT_KEYS = (
    "order_no",
    "query_scope",
    "product",
    "refund_amount",
    "after_sale_action",
    "pending_intent",
    "missing_slot",
)

_MAX_ORDER_LINES = 10
_MAX_ITEM_NAMES = 5
_MAX_AFTER_SALE_RECORDS = 5


@dataclass(frozen=True)
class FormattedSlots:
    text: str
    keys: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True)
class FormattedToolFacts:
    text: str
    sections: tuple[str, ...]
    ignored_sections: tuple[str, ...]
    truncated: bool


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped = dump(mode="json")
        if isinstance(dumped, Mapping):
            return dumped
    return None


def _as_sequence(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    return str(value).strip()


def _money_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return _text(value)
    if not amount.is_finite():
        return ""
    return f"{amount:.2f}"


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    text = _text(value)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return parsed.strftime("%Y-%m-%d %H:%M")


def _items_text(items: Any) -> str:
    values = _as_sequence(items)
    if not values:
        return ""

    names: list[str] = []
    for item in values[:_MAX_ITEM_NAMES]:
        mapping = _as_mapping(item)
        if mapping is None:
            continue
        name = _text(mapping.get("product_name") or mapping.get("name"))
        if not name:
            continue
        quantity = _text(mapping.get("quantity"))
        names.append(f"{name} x{quantity}" if quantity and quantity != "1" else name)

    if len(values) > _MAX_ITEM_NAMES:
        names.append(f"等 {len(values)} 件商品")
    return "、".join(names)


def _logistics_text(logistics: Any) -> str:
    events = _as_sequence(logistics)
    if not events:
        return ""
    latest = _as_mapping(events[-1])
    if latest is None:
        return ""
    status = _text(latest.get("status"))
    location = _text(latest.get("location"))
    description = _text(latest.get("description"))
    event_time = _date_text(latest.get("event_time"))

    details = ""
    if location:
        details += f"（{location}）"
    if description:
        details += description
    if event_time:
        details = f"{details} {event_time}".strip()
    return f"{status}{details}".strip()


def _format_order_line(order: Mapping[str, Any], *, index: int | None = None) -> str:
    order_no = _text(order.get("order_no")) or "订单号未记录"
    status = _text(order.get("status")) or "未记录"
    amount = _money_text(order.get("total_amount"))
    created_at = _date_text(order.get("created_at"))
    items = _items_text(order.get("items"))
    logistics = _logistics_text(order.get("logistics"))

    parts = [f"状态「{status}」"]
    if amount:
        parts.append(f"金额 ¥{amount} 元")
    if created_at:
        parts.append(f"下单时间 {created_at}")
    if items:
        parts.append(f"商品：{items}")
    if logistics:
        parts.append(f"最新物流：{logistics}")

    prefix = f"{index}. " if index is not None else ""
    return f"{prefix}{order_no}：" + "；".join(parts)


def _format_orders(orders: Any) -> tuple[str, str]:
    values = _as_sequence(orders)
    if not values:
        return "订单：当前账号没有订单记录。", "orders"

    lines = [f"订单 {len(values)} 笔："]
    for index, order in enumerate(values[:_MAX_ORDER_LINES], start=1):
        mapping = _as_mapping(order)
        if mapping is None:
            continue
        lines.append(_format_order_line(mapping, index=index))
    if len(values) > _MAX_ORDER_LINES:
        lines.append(f"仅展示前 {_MAX_ORDER_LINES} 笔。")
    return "\n".join(lines), "orders"


def _format_order(order: Any) -> tuple[str, str] | None:
    mapping = _as_mapping(order)
    if mapping is None:
        return None
    return f"订单：{_format_order_line(mapping)}", "order"


def _format_product(product: Any) -> tuple[str, str] | None:
    mapping = _as_mapping(product)
    if mapping is None:
        return None

    name = _text(mapping.get("name")) or "商品名称未记录"
    sku = _text(mapping.get("sku"))
    price = _money_text(mapping.get("price"))
    stock = _text(mapping.get("stock"))

    parts = [f"商品「{name}」"]
    if sku:
        parts.append(f"SKU：{sku}")
    if price:
        parts.append(f"价格 ¥{price} 元")
    if stock:
        parts.append(f"库存 {stock} 件")
    parts.append("价格与库存以下单页面为准")
    return "；".join(parts), "product"


def _format_after_sale_records(records: Any) -> tuple[str, str] | None:
    values = _as_sequence(records)
    if not values:
        return "售后：暂未查询到售后记录。", "after_sale"

    lines = [f"售后记录 {len(values)} 条："]
    for index, record in enumerate(values[:_MAX_AFTER_SALE_RECORDS], start=1):
        mapping = _as_mapping(record)
        if mapping is None:
            continue
        service_type = _text(mapping.get("service_type")) or "未记录"
        status = _text(mapping.get("status")) or "未记录"
        refund = _money_text(mapping.get("refund_amount"))
        reason = _text(mapping.get("reason"))
        created_at = _date_text(mapping.get("created_at"))

        parts = [f"类型：{service_type}", f"状态：{status}"]
        if refund:
            parts.append(f"退款金额：¥{refund} 元")
        if reason:
            parts.append(f"原因：{reason}")
        if created_at:
            parts.append(f"申请时间：{created_at}")
        lines.append(f"{index}. " + "；".join(parts))
    if len(values) > _MAX_AFTER_SALE_RECORDS:
        lines.append(f"仅展示前 {_MAX_AFTER_SALE_RECORDS} 条。")
    return "\n".join(lines), "after_sale"


def _format_knowledge(knowledge: Any) -> tuple[str, str] | None:
    mapping = _as_mapping(knowledge)
    if mapping is None:
        return None
    status = _text(mapping.get("status")) or "未知"
    parent_ids = _as_sequence(mapping.get("parent_ids"))
    text = f"知识检索：状态 {status}"
    if parent_ids:
        text += f"，命中 {len(parent_ids)} 条资料"
    return text, "knowledge"


def _format_weather(weather: Any) -> tuple[str, str] | None:
    mapping = _as_mapping(weather)
    if mapping is None:
        return None
    status = _text(mapping.get("status")) or "unknown"
    city = _text(mapping.get("city"))
    data = _text(mapping.get("data"))

    if status == "success":
        return f"天气：{city}；{data}" if city else f"天气：{data}", "weather"
    if status == "missing_city":
        return "天气：缺少城市，无法查询。", "weather"
    text = "天气：暂时不可用"
    if city:
        text += f"（城市：{city}）"
    return text, "weather"


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def format_slots(
    slots: Mapping[str, Any] | None, *, max_chars: int = 300
) -> FormattedSlots:
    """Format allowlisted conversation slots as readable key/value lines."""
    if not slots:
        return FormattedSlots("", (), False)

    lines: list[str] = []
    keys: list[str] = []
    for key in _SLOT_KEYS:
        value = slots.get(key)
        if not _has_value(value) or isinstance(value, (dict, list, tuple)):
            continue
        text = _text(value)
        if not text:
            continue
        lines.append(f"- {key}: {text}")
        keys.append(key)

    text, truncated = truncate_text("\n".join(lines), max_chars)
    return FormattedSlots(text=text, keys=tuple(keys), truncated=truncated)


def format_tool_facts(
    evidence: Mapping[str, Any] | None, *, max_chars: int = 1200
) -> FormattedToolFacts:
    """Format known tool evidence into compact business facts.

    Unknown sections are omitted rather than dumped. Their names are returned
    in ``ignored_sections`` so a missing formatter is visible in diagnostics.
    """
    if not isinstance(evidence, Mapping) or not evidence:
        return FormattedToolFacts("", (), (), False)

    sections: list[str] = []
    used: list[str] = []
    recognized: set[str] = set()

    if "orders" in evidence:
        recognized.add("orders")
        text, name = _format_orders(evidence.get("orders"))
        sections.append(text)
        used.append(name)
    if "order" in evidence:
        recognized.add("order")
        formatted = _format_order(evidence.get("order"))
        if formatted:
            text, name = formatted
            sections.append(text)
            used.append(name)
    if "product" in evidence:
        recognized.add("product")
        formatted = _format_product(evidence.get("product"))
        if formatted:
            text, name = formatted
            sections.append(text)
            used.append(name)
    if "after_sale" in evidence:
        recognized.add("after_sale")
        formatted = _format_after_sale_records(evidence.get("after_sale"))
        if formatted:
            text, name = formatted
            sections.append(text)
            used.append(name)
    if "knowledge" in evidence:
        recognized.add("knowledge")
        formatted = _format_knowledge(evidence.get("knowledge"))
        if formatted:
            text, name = formatted
            sections.append(text)
            used.append(name)
    if "weather" in evidence:
        recognized.add("weather")
        formatted = _format_weather(evidence.get("weather"))
        if formatted:
            text, name = formatted
            sections.append(text)
            used.append(name)

    ignored: list[str] = []
    for key, value in evidence.items():
        key_text = str(key)
        if key_text in recognized:
            continue
        if key_text in {"threshold", "tool"}:
            # Known policy metadata, intentionally not sent to the answer model.
            ignored.append(key_text)
            continue
        if _has_value(value):
            ignored.append(key_text)

    text, truncated = truncate_text("\n".join(sections).strip(), max_chars)
    return FormattedToolFacts(
        text=text,
        sections=tuple(used),
        ignored_sections=tuple(ignored),
        truncated=truncated,
    )
