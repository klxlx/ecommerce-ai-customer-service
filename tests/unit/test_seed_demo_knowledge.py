"""Startup behavior for demo knowledge and optional PDF indexing."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.rag.indexer import IndexResult


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_seed_env_flags_default_to_demo_on_and_pdf_off(monkeypatch):
    from scripts.seed_demo import _env_flag

    monkeypatch.delenv("SEED_DEMO_KNOWLEDGE", raising=False)
    monkeypatch.delenv("SEED_INDEX_KNOWLEDGE", raising=False)

    assert _env_flag("SEED_DEMO_KNOWLEDGE", default=True) is True
    assert _env_flag("SEED_INDEX_KNOWLEDGE", default=False) is False


def test_seed_startup_warns_but_does_not_fail_when_pdfs_are_missing(monkeypatch):
    import scripts.seed_demo as seed_demo

    async def _seed_users(session):
        del session
        return {"customer": object()}

    async def _seed_products(session):
        del session
        return {"EB-PRO": object()}

    async def _seed_orders(session, users, products):
        del session, users, products
        return {"ORD-1": object()}

    async def _seed_after_sales(session, orders):
        del session, orders

    async def _demo_index(*args, **kwargs):
        del args, kwargs
        return IndexResult(documents_seen=8, documents_indexed=8)

    async def _pdf_index(*args, **kwargs):
        del args, kwargs
        return IndexResult(
            documents_seen=10,
            documents_indexed=0,
            errors=["missing PDF"],
        )

    monkeypatch.setenv("SEED_DEMO_KNOWLEDGE", "1")
    monkeypatch.setenv("SEED_INDEX_KNOWLEDGE", "1")
    monkeypatch.setattr(
        seed_demo,
        "get_settings",
        lambda: SimpleNamespace(
            database_url="postgresql+psycopg://test/test",
            embedding_model="fake-1024",
        ),
    )
    monkeypatch.setattr(
        seed_demo,
        "build_container",
        lambda settings: SimpleNamespace(
            providers=SimpleNamespace(embeddings=object())
        ),
    )
    monkeypatch.setattr(seed_demo, "create_session_factory", lambda url: _SessionContext)
    monkeypatch.setattr(seed_demo, "_seed_users", _seed_users)
    monkeypatch.setattr(seed_demo, "_seed_products", _seed_products)
    monkeypatch.setattr(seed_demo, "_seed_orders", _seed_orders)
    monkeypatch.setattr(seed_demo, "_seed_after_sales", _seed_after_sales)
    monkeypatch.setattr(seed_demo, "index_registered_markdowns", _demo_index)
    monkeypatch.setattr(seed_demo, "index_registered_pdfs", _pdf_index)

    assert asyncio.run(seed_demo.main()) == 0
