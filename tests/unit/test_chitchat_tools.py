"""Local time and weather MCP tool contracts."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from app.core.config import get_settings
from app.tools.local_time import get_local_time
from app.tools.weather import (
    MCPWeatherClient,
    WeatherUnavailable,
    extract_weather_city,
    is_local_time_query,
    is_weather_query,
)


def test_local_time_uses_shanghai_timezone_and_chinese_weekday():
    fact = get_local_time(datetime(2026, 11, 19, 6, 30, tzinfo=timezone.utc))

    assert fact == {
        "timezone": "Asia/Shanghai",
        "date": "2026-11-19",
        "time": "14:30:00",
        "weekday": "星期四",
        "text": "2026年11月19日 14:30:00（星期四，Asia/Shanghai）",
    }


def test_weather_and_time_intent_markers():
    assert is_weather_query("上海今天会下雨吗") is True
    assert is_local_time_query("现在几点了") is True
    assert is_local_time_query("时间") is True
    assert is_local_time_query("现在时间呢") is True
    assert is_local_time_query("当前是什么时间") is True
    assert is_local_time_query("今天几号") is True
    assert is_local_time_query("订单什么时候到") is False
    assert is_local_time_query("退款什么时候到账") is False


def test_extract_weather_city_from_common_phrases():
    assert extract_weather_city("上海天气怎么样") == "上海"
    assert extract_weather_city("广州天气") == "广州"
    assert extract_weather_city("查一下北京市今天天气") == "北京"
    assert extract_weather_city("今天天气怎么样") == ""


@pytest.mark.anyio
async def test_mcp_weather_client_initializes_session_and_calls_weather_tool():
    requests: list[dict] = []
    session_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append(body)
        if request.headers.get("mcp-session-id"):
            session_headers.append(request.headers["mcp-session-id"])

        method = body.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                request=request,
                headers={"Mcp-Session-Id": "session-1"},
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "serverInfo": {"name": "weather", "version": "1"},
                    },
                },
            )
        if method == "notifications/initialized":
            return httpx.Response(202, request=request)
        if method == "tools/call":
            return httpx.Response(
                200,
                request=request,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "content": [
                            {"type": "text", "text": "上海：晴，26℃"}
                        ]
                    },
                },
            )
        raise AssertionError(f"unexpected method: {method}")

    settings = get_settings().model_copy(
        update={
            "weather_mcp_enabled": True,
            "weather_mcp_url": "https://weather.test/mcp",
            "weather_mcp_tool": "get_weather",
            "weather_mcp_timeout_seconds": 3.0,
        }
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MCPWeatherClient(settings, client=http_client)
    try:
        result = await client.query("上海")
    finally:
        await http_client.aclose()

    assert result == "上海：晴，26℃"
    assert [item["method"] for item in requests] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]
    assert requests[-1]["params"] == {
        "name": "get_weather",
        "arguments": {"city": "上海"},
    }
    assert session_headers == ["session-1", "session-1"]


@pytest.mark.anyio
async def test_mcp_weather_client_rejects_unconfigured_service():
    settings = get_settings().model_copy(
        update={"weather_mcp_enabled": False, "weather_mcp_url": ""}
    )

    with pytest.raises(WeatherUnavailable):
        await MCPWeatherClient(settings).query("上海")

