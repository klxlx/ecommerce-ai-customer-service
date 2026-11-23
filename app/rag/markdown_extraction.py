"""Extraction for the small Markdown knowledge bundle shipped with the repo.

The demo bundle is intentionally separate from the legacy ``manifest.json`` so
that unverified historical documents are never silently promoted into the
production knowledge base.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.rag.pdf_extraction import ExtractedDocument, ExtractedParent

_HEADING = re.compile(r"^##\s+(.+?)\s*$")


@dataclass(frozen=True)
class MarkdownSource:
    source_id: str
    file_name: str
    title: str
    kb_type: str
    applicability: str
    effective_date: str | None


@dataclass(frozen=True)
class MarkdownRegistry:
    root: str
    sources: tuple[MarkdownSource, ...]


def load_markdown_registry(registry_path: str | Path) -> MarkdownRegistry:
    """Load and validate the demo Markdown registry."""
    path = Path(registry_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    root = str(data["root"])
    sources = tuple(
        MarkdownSource(
            source_id=entry["source_id"],
            file_name=entry["file_name"],
            title=entry["title"],
            kb_type=entry["kb_type"],
            applicability=entry.get("applicability", ""),
            effective_date=entry.get("effective_date"),
        )
        for entry in data["sources"]
    )
    if not sources:
        raise ValueError("demo Markdown registry is empty")
    if len({source.source_id for source in sources}) != len(sources):
        raise ValueError("duplicate source_id in demo Markdown registry")
    if len({source.file_name for source in sources}) != len(sources):
        raise ValueError("duplicate file_name in demo Markdown registry")
    return MarkdownRegistry(root=root, sources=sources)


def extract_markdown(
    source: MarkdownSource,
    knowledge_root: str | Path,
) -> ExtractedDocument:
    """Convert one Markdown file into indexed parent sections."""
    path = Path(knowledge_root) / source.file_name
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig").replace("\r\n", "\n").strip()
    if not text:
        raise ValueError(f"empty Markdown document: {source.file_name}")

    parents = _split_markdown_sections(text, source.title)
    if not parents:
        raise ValueError(f"no extractable Markdown sections: {source.file_name}")

    return ExtractedDocument(
        source_id=source.source_id,
        file_name=source.file_name,
        title=source.title,
        kb_type=source.kb_type,
        applicability=source.applicability,
        effective_date=source.effective_date,
        checksum=hashlib.sha256(raw).hexdigest(),
        page_count=0,
        extraction_strategy="markdown",
        parents=parents,
        source_kind="text",
        original_filename=path.name,
    )


def _split_markdown_sections(
    text: str,
    document_title: str,
) -> tuple[ExtractedParent, ...]:
    """Split text by level-2 headings, keeping an intro section if present."""
    sections: list[tuple[str, list[str]]] = []
    current_heading = document_title
    current_lines: list[str] = []

    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            if _has_body(current_lines):
                sections.append((current_heading, current_lines))
            current_heading = match.group(1).strip()
            current_lines = [line]
            continue
        current_lines.append(line)

    if _has_body(current_lines):
        sections.append((current_heading, current_lines))

    parents: list[ExtractedParent] = []
    for index, (heading, lines) in enumerate(sections):
        content = "\n".join(lines).strip()
        if not content:
            continue
        section_path = (
            document_title
            if heading == document_title
            else f"{document_title} / {heading}"
        )
        parents.append(
            ExtractedParent(
                index=index,
                content=content,
                section_path=section_path,
                page_start=None,
                page_end=None,
            )
        )
    return tuple(parents)


def _has_body(lines: list[str]) -> bool:
    """Return True when a section carries more than its heading."""
    meaningful = [
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return bool(meaningful)
