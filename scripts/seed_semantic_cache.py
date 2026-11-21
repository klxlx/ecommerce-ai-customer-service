"""Import curated FAQ entries into the pgvector semantic cache.

Usage:
    python -m scripts.seed_semantic_cache --file data/semantic_cache_seed.json
    python -m scripts.seed_semantic_cache --file data/semantic_cache_seed.csv
    python -m scripts.seed_semantic_cache --clear
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.bootstrap import build_container
from app.core.config import get_settings
from app.db.session import create_session_factory
from app.services import semantic_cache as cache_service


def _read_entries(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("JSON 文件根节点必须是数组")
        return cache_service.parse_seed_entries(raw)
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        entries = []
        for row in rows:
            sources_raw = (row.get("sources") or "").strip()
            sources = json.loads(sources_raw) if sources_raw else []
            entries.append(
                {
                    "question": row.get("question", ""),
                    "answer": row.get("answer", ""),
                    "kb_type": row.get("kb_type", "policy"),
                    "sources": sources,
                    "expires_at": row.get("expires_at") or None,
                }
            )
        return cache_service.parse_seed_entries(entries)
    raise ValueError("只支持 .json 或 .csv 文件")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path)
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    container = build_container(settings)
    factory = create_session_factory(settings.database_url)
    async with factory() as session:
        if args.clear:
            removed = await cache_service.clear(session)
            await session.commit()
            print(f"cleared={removed}")
            return 0
        if args.file is None:
            parser.error("需要 --file 或 --clear")
        entries = _read_entries(args.file)
        imported = await cache_service.import_entries(
            session,
            entries,
            container.cache_embeddings or container.providers.embeddings,
        )
        await session.commit()
        print(f"imported={imported} file={args.file}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
