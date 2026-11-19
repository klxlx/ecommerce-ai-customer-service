"""Deterministic parent/child splitting for knowledge documents.

A source document is first split into parent blocks on level-2 headings, then
each parent is chunked into overlapping child chunks. Dynamic business facts
(price, stock, order, logistics) must never be embedded, so any child that
declares such a field is rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_PARENT_CHARS = 4_000

DYNAMIC_FIELD_PATTERN = re.compile(
    r"\{\{\s*(price|stock|inventory|order_no|orderid|logistics|tracking|status|quantity)\s*\}\}",
    re.IGNORECASE,
)

# A conservative guard for literal "price: 99" / "库存 100" style facts.
DYNAMIC_LITERAL_PATTERN = re.compile(
    r"(价格|售价|库存|订单号|物流|运单号)\s*[:：]\s*[0-9￥¥]", re.IGNORECASE
)


@dataclass(frozen=True)
class ParentBlock:
    index: int
    content: str
    heading: str


def split_parent_text(text: str, max_chars: int = MAX_PARENT_CHARS) -> list[str]:
    """Split text into bounded blocks, preferring natural text boundaries."""
    text = text.strip()
    if not text:
        return []
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return [text]

    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        pieces = _split_oversized_segment(paragraph, max_chars=max_chars)
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def _split_oversized_segment(text: str, *, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    separators = ("\n", "。", "；", "！", "？", ";", ". ")
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        positions = [
            (position, separator)
            for separator in separators
            if (position := window.rfind(separator)) >= 0
        ]
        split_at = (
            max(positions, key=lambda item: item[0])[0]
            + len(max(positions, key=lambda item: item[0])[1])
            if positions
            else max_chars
        )
        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def split_parents(text: str) -> list[ParentBlock]:
    """Split a document into parent blocks on level-2 headings."""
    text = text.strip()
    if not text:
        return []
    parts = re.split(r"(?m)^##\s+(.+)$", text)
    if len(parts) == 1:
        return [
            ParentBlock(index=index, content=content, heading="")
            for index, content in enumerate(split_parent_text(text))
        ]
    blocks: list[ParentBlock] = []
    # parts[0] is text before the first heading.
    if parts[0].strip():
        for content in split_parent_text(parts[0]):
            blocks.append(
                ParentBlock(index=len(blocks), content=content, heading="")
            )
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if body:
            for content in split_parent_text(body):
                blocks.append(
                    ParentBlock(
                        index=len(blocks),
                        content=content,
                        heading=heading,
                    )
                )
    if not blocks:
        return [
            ParentBlock(index=index, content=content, heading="")
            for index, content in enumerate(split_parent_text(text))
        ]
    return blocks


def split_children(content: str, chunk_size: int = 400, overlap: int = 80) -> list[str]:
    """Split a parent block into overlapping child chunks."""
    content = content.strip()
    if not content:
        return []
    if len(content) <= chunk_size:
        return [content]
    step = chunk_size - overlap
    chunks: list[str] = []
    start = 0
    while start < len(content):
        end = min(start + chunk_size, len(content))
        chunks.append(content[start:end].strip())
        if end == len(content):
            break
        start += step
    return [c for c in chunks if c]


def assert_no_dynamic_fields(text: str) -> None:
    """Raise ValueError when ``text`` declares a dynamic business fact."""
    if DYNAMIC_FIELD_PATTERN.search(text) or DYNAMIC_LITERAL_PATTERN.search(text):
        raise ValueError(
            "knowledge content declares a dynamic field (price/stock/order/logistics)"
        )
