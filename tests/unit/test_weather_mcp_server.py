"""Weather MCP server behavior backed by Open-Meteo."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI

from app.api.routes import weather_mcp


@pytest.mark.anyio
async def test_fetch_weather_formats_open_meteo_current_conditions():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(
                200,
                request=request,
                json={
                    "results": [
                        {
                            "name": "广州市",
                            "admin1": "广东省",
                            "latitude": 23.1167,
                            "longitude": 113.25,
                        }
                    ]
                },
            )
        if request.url.host == "api.open-meteo.com":
            return httpx.Response(
                200,
                request=request,
                json={
                    "current": {
                        "time": "2026-11-19T23:15",
                        "temperature_2m": 17.5,
                        "apparent_temperature": 15.2,
                        "weather_code": 3,
                        "wind_speed_10m": 15.3,
                        "wind_direction_10m": 20,
                    }
                },
            )
        raise AssertionError(f"unexpected URL: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await weather_mcp.fetch_weather("广州", client=client)
    finally:
        await client.aclose()

    assert result.startswith("广州市（广东省）：阴，17.5℃")
    assert "体感 15.2℃" in result
    assert "观察时间 2026-11-19 23:15" in result


@pytest.mark.anyio
async def test_weather_mcp_route_supports_initialize_and_get_weather(monkeypatch):
    async def fake_fetch_weather(city: str) -> str:
        return f"{city}：晴，26℃"

    monkeypatch.setattr(weather_mcp, "fetch_weather", fake_fetch_weather)
    app = FastAPI()
    app.include_router(weather_mcp.router, prefix="/mcp/weather")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://weather.test",
    ) as client:
        initialize = await client.post(
            "/mcp/weather",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        initialized = await client.post(
            "/mcp/weather",
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
        )
        response = await client.post(
            "/mcp/weather",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "get_weather",
                    "arguments": {"city": "广州"},
                },
            },
        )

    assert initialize.status_code == 200
    assert initialize.headers["Mcp-Session-Id"]
    assert initialized.status_code == 202
    payload = response.json()
    assert payload["result"]["content"] == [
        {"type": "text", "text": "广州：晴，26℃"}
    ]
    assert json.loads(response.request.content)["params"]["arguments"] == {
        "city": "广州"
    }
