"""Smoke test for the documented local launcher (no ``--reload``).

The launcher must select the Windows selector event loop before Uvicorn creates
its loop; otherwise login fails with psycopg's ProactorEventLoop error. This
test starts exactly the documented command in a subprocess and verifies health
and a real login.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from tests.helpers import create_user

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_log(log_file) -> str:
    log_file.flush()
    log_file.seek(0)
    return log_file.read()


@pytest.mark.anyio
async def test_local_launcher_serves_health_and_login(db_session):
    await create_user(db_session, "smoke-user", "password123")

    port = _free_port()
    env = os.environ.copy()
    env["MOCK_PROVIDERS"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    log_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "scripts.serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    try:
        deadline = time.monotonic() + 30
        health = None
        last_error: Exception | None = None
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=2.0
        ) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail(
                        "local launcher exited early\n" + _read_log(log_file)
                    )
                try:
                    response = await client.get("/health")
                    if response.status_code == 200:
                        health = response
                        break
                except httpx.TransportError as exc:
                    last_error = exc
                await asyncio.sleep(0.25)

            assert health is not None, (
                f"server did not become ready: {last_error}\n" + _read_log(log_file)
            )
            body = health.json()
            assert body["status"] == "ok"
            assert body["database"] == "ok"

            login = await client.post(
                "/api/auth/login",
                json={"username": "smoke-user", "password": "password123"},
            )
            assert login.status_code == 200, login.text
            assert login.json()["token"]
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        log_file.close()
