"""Unit tests for the pure parent-expansion and dedupe logic."""

from __future__ import annotations

from types import SimpleNamespace

from app.rag.retriever import ChunkCandidate, expand_parents


def _parent(pid: int, source_id: int, content: str, version_id: int):
    return SimpleNamespace(
        id=pid,
        source_id=source_id,
        content=content,
        document_version_id=version_id,
        page_start=2,
        page_end=3,
        section_path="第一章 使用 > 第一节 开机",
    )


def _source(sid: int, name: str, uri: str, kb_type: str):
    return SimpleNamespace(id=sid, name=name, source_uri=uri, kb_type=kb_type)


def _version(vid: int, file_name: str, checksum: str):
    return SimpleNamespace(id=vid, pdf_file=file_name, checksum=checksum)


def test_expand_parents_dedupes_and_uses_full_parent_text():
    parents = {
        1: _parent(1, 10, "父块一完整内容：这里是完整的段落正文。", 100),
        2: _parent(2, 20, "父块二完整内容：这里是另一个完整段落。", 200),
    }
    sources = {
        10: _source(10, "产品A", "p/a.md", "product"),
        20: _source(20, "产品B", "p/b.md", "product"),
    }
    versions = {
        100: _version(100, "a.pdf", "a" * 64),
        200: _version(200, "b.pdf", "b" * 64),
    }
    candidates = [
        ChunkCandidate(chunk_id=2, parent_id=1, content="子块A2", score=0.9),
        ChunkCandidate(chunk_id=1, parent_id=1, content="子块A1", score=0.5),
        ChunkCandidate(chunk_id=3, parent_id=2, content="子块B1", score=0.4),
    ]

    result = expand_parents(candidates, parents, sources, versions)

    # Each parent appears exactly once despite two children from parent 1.
    assert result.parent_ids == [1, 2]
    assert len(result.sources) == 2
    assert result.sources[0].parent_id == 1
    assert result.sources[0].chunk_id == 2  # best-ranked child wins
    assert result.sources[0].title == "产品A"
    assert result.sources[0].source_uri == "p/a.md"
    assert result.sources[0].document_version_id == 100
    assert result.sources[0].file_name == "a.pdf"
    assert result.sources[0].checksum == "a" * 64
    assert result.sources[0].page_start == 2
    assert result.sources[0].page_end == 3
    assert result.sources[0].section_path == "第一章 使用 > 第一节 开机"

    # Full parent text, not child-only text, is returned.
    assert "父块一完整内容" in result.context
    assert "父块二完整内容" in result.context
    assert "子块A1" not in result.context


def test_expand_parents_empty_is_explicit():
    result = expand_parents([], {}, {}, {})
    assert result.is_empty is True
    assert result.sources == []
    assert result.context == ""


def test_expand_parents_respects_context_character_budget():
    parents = {
        1: _parent(1, 10, "甲" * 300, 100),
        2: _parent(2, 20, "乙" * 300, 200),
    }
    sources = {
        10: _source(10, "资料甲", "p/a.md", "product"),
        20: _source(20, "资料乙", "p/b.md", "product"),
    }
    versions = {
        100: _version(100, "a.pdf", "a" * 64),
        200: _version(200, "b.pdf", "b" * 64),
    }
    candidates = [
        ChunkCandidate(chunk_id=1, parent_id=1, content="甲", score=0.9),
        ChunkCandidate(chunk_id=2, parent_id=2, content="乙", score=0.8),
    ]

    result = expand_parents(candidates, parents, sources, versions, max_context_chars=120)

    assert len(result.context) <= 120
    assert result.parent_ids == [1]
    assert [source.parent_id for source in result.sources] == [1]
    assert result.sources[0].chunk_id == 1


def test_expand_parents_keeps_multiple_parents_when_budget_allows():
    parents = {
        1: _parent(1, 10, "甲" * 20, 100),
        2: _parent(2, 20, "乙" * 20, 200),
    }
    sources = {
        10: _source(10, "资料甲", "p/a.md", "product"),
        20: _source(20, "资料乙", "p/b.md", "product"),
    }
    versions = {
        100: _version(100, "a.pdf", "a" * 64),
        200: _version(200, "b.pdf", "b" * 64),
    }
    candidates = [
        ChunkCandidate(chunk_id=1, parent_id=1, content="甲", score=0.9),
        ChunkCandidate(chunk_id=2, parent_id=2, content="乙", score=0.8),
    ]

    result = expand_parents(candidates, parents, sources, versions, max_context_chars=120)

    assert result.parent_ids == [1, 2]
    assert [source.parent_id for source in result.sources] == [1, 2]
    assert len(result.context) <= 120
