"""Audit contracts for official PDF sources and retired Markdown knowledge."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "data" / "knowledge" / "pdf_sources.json"
LEGACY_MANIFEST_PATH = ROOT / "data" / "knowledge" / "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_pdf_registry_tracks_all_ten_official_sources_once():
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    sources = registry["sources"]

    assert len(sources) == 10
    assert len({source["source_id"] for source in sources}) == 10
    assert len({source["file_name"] for source in sources}) == 10
    assert len({source["checksum"] for source in sources}) == 10
    assert sum(source["kb_type"] == "product" for source in sources) == 6
    assert sum(source["kb_type"] == "policy" for source in sources) == 4


def test_pdf_registry_files_match_registered_checksum_and_page_count():
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    pdf_root = ROOT / registry["pdf_root"]

    for source in registry["sources"]:
        path = pdf_root / source["file_name"]
        assert path.is_file(), source["file_name"]
        assert _sha256(path) == source["checksum"], source["file_name"]
        assert source["page_count"] > 0
        assert source["title"]
        assert source["applicability"]
        assert source["extraction_strategy"] in {
            "huawei_outline",
            "jd_clauses",
        }


def test_policy_registry_has_effective_dates_not_in_the_future():
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    policy_sources = [
        source for source in registry["sources"] if source["kb_type"] == "policy"
    ]

    assert {
        source["effective_date"] for source in policy_sources
    } == {
        "2024-05-08",
        "2026-05-07",
        "2026-10-19",
        "2024-12-26",
    }
    assert all(source["effective_date"] <= "2026-11-18" for source in policy_sources)


def test_legacy_markdown_manifest_is_disabled_and_checksummed():
    manifest = json.loads(LEGACY_MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == 2
    assert manifest["status"] == "legacy"
    assert len(manifest["sources"]) == 8
    assert all(source["legacy"] is True for source in manifest["sources"])
    assert all(source["reason"] == "unverified_source" for source in manifest["sources"])

    base_dir = LEGACY_MANIFEST_PATH.parent
    for source in manifest["sources"]:
        path = base_dir / source["uri"]
        assert path.is_file(), source["uri"]
        assert _sha256(path) == source["checksum"], source["uri"]


def test_legacy_audit_record_exists_in_chinese():
    audit_path = ROOT / "ccb-docs" / "p3-legacy-knowledge-audit.md"
    audit = audit_path.read_text(encoding="utf-8")

    assert "旧 Markdown 知识审计" in audit
    assert "不直接清空" in audit
    assert "不参与正式检索" in audit
