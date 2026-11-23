"""Contract tests for the small Markdown demo knowledge bundle."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "data" / "knowledge" / "demo_sources.json"


def test_demo_markdown_registry_has_eight_existing_sources():
    from app.rag.markdown_extraction import load_markdown_registry

    registry = load_markdown_registry(REGISTRY_PATH)

    assert len(registry.sources) == 8
    assert {source.kb_type for source in registry.sources} == {"policy", "product"}
    for source in registry.sources:
        assert (ROOT / registry.root / source.file_name).is_file()


def test_extract_markdown_splits_sections_and_builds_traceable_metadata():
    from app.rag.markdown_extraction import (
        extract_markdown,
        load_markdown_registry,
    )

    registry = load_markdown_registry(REGISTRY_PATH)
    source = next(
        item for item in registry.sources if item.source_id == "demo-policy-return"
    )

    document = extract_markdown(source, ROOT / registry.root)

    assert document.source_kind == "text"
    assert document.extraction_strategy == "markdown"
    assert document.page_count == 0
    assert document.checksum
    assert len(document.parents) >= 4
    assert all(parent.content.strip() for parent in document.parents)
    assert document.parents[0].section_path.startswith("退换货政策")
    assert any("退换货时限" in parent.section_path for parent in document.parents)
