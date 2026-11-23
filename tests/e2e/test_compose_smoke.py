"""Contract tests for the Docker Compose deployment.

These inspect ``compose.yaml`` and ``Dockerfile`` for the required services and
startup steps without requiring a live Docker daemon, so they run in normal CI.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_compose_defines_database_and_app_services():
    compose = yaml.safe_load(_read("compose.yaml"))
    services = compose["services"]
    assert "db" in services
    assert "app" in services


def test_database_uses_pgvector_image():
    compose = yaml.safe_load(_read("compose.yaml"))
    image = compose["services"]["db"]["image"]
    assert "pgvector" in image


def test_app_startup_runs_migrations_and_seed():
    content = (
        _read("compose.yaml")
        + "\n"
        + _read("Dockerfile")
        + "\n"
        + _read("docker/entrypoint.sh")
    )
    assert "alembic upgrade" in content
    assert "seed_demo" in content


def test_compose_defaults_to_demo_knowledge_without_pdfs():
    compose = yaml.safe_load(_read("compose.yaml"))
    app = compose["services"]["app"]
    env = app["environment"]

    assert env["SEED_DEMO_KNOWLEDGE"] == "${SEED_DEMO_KNOWLEDGE:-1}"
    assert env["SEED_INDEX_KNOWLEDGE"] == "${SEED_INDEX_KNOWLEDGE:-0}"
    assert any(
        "data/knowledge/pdf_sources" in str(volume)
        for volume in app.get("volumes", [])
    )


def test_app_serves_frontend_and_exposes_port():
    compose = yaml.safe_load(_read("compose.yaml"))
    ports = compose["services"]["app"].get("ports", [])
    assert any("8000" in str(p) for p in ports)


def test_health_url_is_documented():
    readme = _read("README.md")
    assert "/health" in readme


def test_dockerfile_builds_frontend():
    content = _read("Dockerfile")
    assert "npm" in content or "node" in content
    assert "pip" in content
