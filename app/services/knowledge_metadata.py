"""Detect static policy metadata from trusted registries or knowledge text."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

REGISTRY_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "knowledge" / "pdf_sources.json"
)

_DATE_VALUE = (
    r"(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?"
    r"|\d{4}年\d{1,2}月\d{1,2}日)"
)
_SCOPE_LABEL_PATTERN = re.compile(
    r"(?m)^[ \t]*(?:适用范围|适用对象|适用商品|适用渠道)"
    r"[ \t]*[:：][ \t]*([^\n]+?)[ \t]*$"
)
_INLINE_SCOPE_PATTERN = re.compile(r"仅适用于([^。\n；;]+)")
_EFFECTIVE_LABEL_PATTERN = re.compile(
    rf"生效日期[ \t]*[:：][ \t]*({_DATE_VALUE})"
)
_EFFECTIVE_SENTENCE_PATTERN = re.compile(
    rf"(?:自|于)?[ \t]*({_DATE_VALUE})[ \t]*(?:起)?"
    r"(?:施行|生效|实施|执行)"
)


@dataclass(frozen=True)
class KnowledgeMetadata:
    applicability: str = ""
    effective_date: date | None = None


def detect_knowledge_metadata(
    content: str,
    *,
    source_key: str | None = None,
) -> KnowledgeMetadata:
    """Return non-authoritative metadata inferred from trusted static content."""
    registered = _registered_metadata(source_key)
    if registered is not None:
        return registered

    applicability = _detect_applicability(content)
    effective_date = _detect_effective_date(content)
    return KnowledgeMetadata(
        applicability=applicability,
        effective_date=effective_date,
    )


def _detect_applicability(content: str) -> str:
    labelled = _SCOPE_LABEL_PATTERN.search(content)
    if labelled is not None:
        return _normalise_applicability(labelled.group(1))

    inline = _INLINE_SCOPE_PATTERN.search(content)
    if inline is not None:
        return _normalise_applicability(inline.group(1))
    return ""


def _normalise_applicability(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t:：。；;")[:2000]


def _detect_effective_date(content: str) -> date | None:
    for pattern in (_EFFECTIVE_LABEL_PATTERN, _EFFECTIVE_SENTENCE_PATTERN):
        for match in pattern.finditer(content):
            parsed = _parse_date(match.group(1))
            if parsed is not None:
                return parsed
    return None


def _parse_date(value: str) -> date | None:
    match = re.fullmatch(
        r"\s*(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?\s*",
        value,
    )
    if match is None:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _registered_metadata(source_key: str | None) -> KnowledgeMetadata | None:
    if not source_key or not source_key.startswith("file-"):
        return None
    checksum = source_key.removeprefix("file-")
    source = _registered_sources().get(checksum)
    if source is None:
        return None
    return KnowledgeMetadata(
        applicability=str(source.get("applicability") or "").strip()[:2000],
        effective_date=_parse_date(str(source.get("effective_date") or "")),
    )


@lru_cache(maxsize=1)
def _registered_sources() -> dict[str, dict]:
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return {
        str(source["checksum"]): source
        for source in data.get("sources", [])
        if isinstance(source, dict) and source.get("checksum")
    }
