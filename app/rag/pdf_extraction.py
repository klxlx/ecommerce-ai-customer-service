"""Traceable PDF extraction for registered product guides and platform rules."""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader

SUPPORTED_STRATEGIES = frozenset({"huawei_outline", "jd_clauses"})
PAGE_MARKER_PATTERN = re.compile(r"\[\[PAGE:\d+]]")

JD_HEADING_PATTERNS = (
    (
        "chapter",
        re.compile(
            r"(?m)^\s*(第[一二三四五六七八九十百零〇0-9]+章"
            r"(?:\s+[^\n]{1,50})?)\s*$"
        ),
    ),
    (
        "section",
        re.compile(
            r"(?m)^\s*(第[一二三四五六七八九十百零〇0-9]+节"
            r"(?:\s+[^\n]{1,50})?)\s*$"
        ),
    ),
    (
        "article",
        re.compile(
            r"(?m)^\s*(第[一二三四五六七八九十百零〇0-9]+条)"
            r"(?=\s|$)"
        ),
    ),
    (
        "table",
        re.compile(
            r"(?m)^\s*(表\s*[0-9一二三四五六七八九十百零〇]+"
            r"(?:\s+[^\n]{1,60})?)\s*$"
        ),
    ),
)


@dataclass(frozen=True)
class PdfSource:
    source_id: str
    file_name: str
    title: str
    kb_type: str
    applicability: str
    extraction_strategy: str
    page_count: int
    effective_date: str | None
    checksum: str


@dataclass(frozen=True)
class ExtractedParent:
    index: int
    content: str
    section_path: str
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True)
class ExtractedDocument:
    source_id: str
    file_name: str
    title: str
    kb_type: str
    applicability: str
    effective_date: str | None
    checksum: str
    page_count: int
    extraction_strategy: str
    parents: tuple[ExtractedParent, ...]
    source_kind: str = "unknown"
    original_filename: str = ""


@dataclass(frozen=True)
class _OutlineEntry:
    title: str
    page: int
    section_path: str
    order: int


def load_pdf_sources(registry_path: str | Path) -> list[PdfSource]:
    """Load and validate the official PDF registry."""
    data = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    sources = [
        PdfSource(
            source_id=entry["source_id"],
            file_name=entry["file_name"],
            title=entry["title"],
            kb_type=entry["kb_type"],
            applicability=entry["applicability"],
            extraction_strategy=entry["extraction_strategy"],
            page_count=int(entry["page_count"]),
            effective_date=entry.get("effective_date"),
            checksum=entry["checksum"],
        )
        for entry in data["sources"]
    ]
    for source in sources:
        if source.extraction_strategy not in SUPPORTED_STRATEGIES:
            raise ValueError(
                f"unsupported extraction strategy: {source.extraction_strategy}"
            )
    if len({source.source_id for source in sources}) != len(sources):
        raise ValueError("duplicate source_id in PDF registry")
    return sources


def extract_pdf(source: PdfSource, pdf_root: str | Path) -> ExtractedDocument:
    """Extract ordered parent sections while verifying the registered file."""
    path = Path(pdf_root) / source.file_name
    checksum = _sha256(path)
    if checksum != source.checksum:
        raise ValueError(f"checksum mismatch for {source.file_name}")

    reader = PdfReader(str(path))
    page_count = len(reader.pages)
    if page_count != source.page_count:
        raise ValueError(
            f"page count mismatch for {source.file_name}: "
            f"{page_count} != {source.page_count}"
        )
    page_texts = tuple(_extract_page_text(page) for page in reader.pages)
    if source.extraction_strategy == "huawei_outline":
        parents = _extract_huawei_outline(reader, page_texts)
    else:
        parents = _extract_jd_clauses(page_texts)

    if not parents:
        raise ValueError(f"no extractable sections in {source.file_name}")

    return ExtractedDocument(
        source_id=source.source_id,
        file_name=source.file_name,
        title=source.title,
        kb_type=source.kb_type,
        applicability=source.applicability,
        effective_date=source.effective_date,
        checksum=checksum,
        page_count=page_count,
        extraction_strategy=source.extraction_strategy,
        parents=tuple(parents),
        source_kind="pdf",
        original_filename=source.file_name,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _extract_page_text(page: Any) -> str:
    return (page.extract_text() or "").strip()


def _normalise_label(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _is_table_of_contents(title: str) -> bool:
    return "目录" in re.sub(r"\s+", "", title)


def _flatten_huawei_outline(
    reader: PdfReader,
    outline: list[Any],
    ancestors: tuple[str, ...] = (),
    order_start: int = 0,
) -> list[_OutlineEntry]:
    entries: list[_OutlineEntry] = []
    order = order_start
    position = 0
    while position < len(outline):
        item = outline[position]
        if isinstance(item, list):
            nested = _flatten_huawei_outline(reader, item, ancestors, order)
            entries.extend(nested)
            order += len(nested)
            position += 1
            continue

        title = _normalise_label(getattr(item, "title", "") or "")
        try:
            page_index = reader.get_destination_page_number(item)
        except (KeyError, TypeError, ValueError):
            page_index = None
        order += 1
        section_path = ancestors
        if (
            title
            and page_index is not None
            and page_index >= 0
            and not _is_table_of_contents(title)
        ):
            section_path = (*ancestors, title)
            entries.append(
                _OutlineEntry(
                    title=title,
                    page=page_index + 1,
                    section_path=" > ".join(section_path),
                    order=order,
                )
            )

        position += 1
        if position < len(outline) and isinstance(outline[position], list):
            nested = _flatten_huawei_outline(
                reader, outline[position], section_path, order
            )
            entries.extend(nested)
            order += len(nested)
            position += 1
    return entries


def _extract_huawei_outline(
    reader: PdfReader, page_texts: tuple[str, ...]
) -> list[ExtractedParent]:
    entries = _flatten_huawei_outline(reader, reader.outline)
    by_page: dict[int, list[_OutlineEntry]] = {}
    for entry in entries:
        if 1 <= entry.page <= len(page_texts):
            by_page.setdefault(entry.page, []).append(entry)

    start_pages = sorted(page for page in by_page if any(page_texts[page - 1 : page]))
    parents: list[ExtractedParent] = []
    for position, page in enumerate(start_pages):
        candidates = by_page[page]
        # Several nested headings can share a page. The last heading gives the
        # most complete section path for the page-level text available in PDF.
        selected = max(candidates, key=lambda entry: entry.order)
        page_end = (
            start_pages[position + 1] - 1
            if position + 1 < len(start_pages)
            else len(page_texts)
        )
        content = "\n\n".join(
            text for text in page_texts[page - 1 : page_end] if text
        ).strip()
        if not content:
            continue
        parents.append(
            ExtractedParent(
                index=len(parents),
                content=content,
                section_path=selected.section_path,
                page_start=page,
                page_end=page_end,
            )
        )
    return parents


def _extract_jd_clauses(page_texts: tuple[str, ...]) -> list[ExtractedParent]:
    full_text, page_starts = _annotate_pages(page_texts)
    headings: list[tuple[int, str, str]] = []
    for kind, pattern in JD_HEADING_PATTERNS:
        for match in pattern.finditer(full_text):
            headings.append((match.start(), kind, _normalise_label(match.group(1))))
    headings.sort(key=lambda item: item[0])

    if not headings:
        return [
            ExtractedParent(
                index=index,
                content=text,
                section_path=f"第 {index + 1} 页",
                page_start=index + 1,
                page_end=index + 1,
            )
            for index, text in enumerate(page_texts)
            if text
        ]

    parents: list[ExtractedParent] = []
    chapter = ""
    section = ""
    for index, (start, kind, heading) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(full_text)
        raw_content = full_text[start:end]
        content = PAGE_MARKER_PATTERN.sub("", raw_content).strip()
        if not content:
            continue
        if kind == "chapter":
            chapter = heading
            section = ""
        elif kind == "section":
            section = heading

        path_parts = [part for part in (chapter, section) if part]
        if kind in {"article", "table"} and heading not in path_parts:
            path_parts.append(heading)
        if not path_parts:
            path_parts.append(heading)

        page_start = _page_at(start, page_starts)
        page_end = _page_at(max(start, end - 1), page_starts)
        parents.append(
            ExtractedParent(
                index=len(parents),
                content=content,
                section_path=" > ".join(path_parts),
                page_start=page_start,
                page_end=page_end,
            )
        )
    return parents


def _annotate_pages(page_texts: tuple[str, ...]) -> tuple[str, list[tuple[int, int]]]:
    parts: list[str] = []
    page_starts: list[tuple[int, int]] = []
    offset = 0
    for page_number, text in enumerate(page_texts, start=1):
        marker = f"\n[[PAGE:{page_number}]]\n"
        parts.append(marker)
        offset += len(marker)
        page_starts.append((offset, page_number))
        parts.append(text)
        offset += len(text)
    return "".join(parts), page_starts


def _page_at(offset: int, page_starts: list[tuple[int, int]]) -> int:
    positions = [position for position, _ in page_starts]
    index = max(0, bisect_right(positions, offset) - 1)
    return page_starts[index][1]
