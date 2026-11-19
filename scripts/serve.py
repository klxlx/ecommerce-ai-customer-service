"""Run the application locally with a psycopg-compatible event loop.

Usage:
    python -m scripts.serve [--host 127.0.0.1] [--port 8000]

Uvicorn selects the Proactor event loop on Windows by default, and modern
Uvicorn passes an explicit ``loop_factory`` to ``asyncio.run``. Changing the
event-loop policy is therefore ignored, and async psycopg cannot run on the
Proactor loop. This launcher runs Uvicorn's server under an explicit selector
loop instead; it is the documented local run path and does not rely on
``--reload``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def _selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the customer-service app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import uvicorn

    config = uvicorn.Config(
        "app.main:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    if sys.platform == "win32":
        asyncio.run(server.serve(), loop_factory=_selector_loop)
    else:
        server.run()


if __name__ == "__main__":
    main()
