"""Read-only weather MCP endpoint backed by Open-Meteo."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

router = APIRouter()

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_WEATHER_LABELS = {
    0: "晴",
    1: "大部晴朗",
    2: "多云",
    3: "阴",
    45: "有雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "强毛毛雨",
    56: "轻微冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "轻微冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "小阵雨",
    81: "中阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷阵雨",
    96: "雷阵雨伴小冰雹",
    99: "雷阵雨伴大冰雹",
}
_WIND_DIRECTIONS = ("北", "东北", "东", "东南", "南", "西南", "西", "西北")


class WeatherServiceUnavailable(RuntimeError):
    """Raised when live weather facts cannot be fetched."""


async def fetch_weather(
    city: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Return current weather for a city using Open-Meteo."""
    city = city.strip()
    if not city:
        raise WeatherServiceUnavailable("city is required")

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=8.0, follow_redirects=True)
    try:
        geocoding = await http.get(
            _GEOCODING_URL,
            params={
                "name": city,
                "count": 1,
                "language": "zh",
                "format": "json",
            },
        )
        geocoding.raise_for_status()
        results = geocoding.json().get("results") or []
        if not results:
            raise WeatherServiceUnavailable("city not found")
        location = results[0]

        forecast = await http.get(
            _FORECAST_URL,
            params={
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "current": (
                    "temperature_2m,apparent_temperature,weather_code,"
                    "wind_speed_10m,wind_direction_10m"
                ),
                "timezone": "Asia/Shanghai",
            },
        )
        forecast.raise_for_status()
        current = forecast.json().get("current") or {}

        name = str(location.get("name") or city)
        region = str(location.get("admin1") or "").strip()
        place = f"{name}（{region}）" if region else name
        code = int(current["weather_code"])
        condition = _WEATHER_LABELS.get(code, f"天气代码 {code}")
        temperature = float(current["temperature_2m"])
        apparent = float(current["apparent_temperature"])
        wind_speed = float(current["wind_speed_10m"])
        wind_direction = _wind_direction(
            float(current["wind_direction_10m"])
        )
        observed = str(current["time"]).replace("T", " ")
        return (
            f"{place}：{condition}，{temperature:g}℃"
            f"（体感 {apparent:g}℃，{wind_direction}风 {wind_speed:g} km/h，"
            f"观察时间 {observed}）"
        )
    except WeatherServiceUnavailable:
        raise
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        raise WeatherServiceUnavailable("weather request failed") from exc
    finally:
        if owns_client:
            await http.aclose()


@router.post("")
async def handle_mcp(request: Request) -> Response:
    """Handle the small Streamable HTTP MCP surface used by the agent."""
    payload = await request.json()
    method = payload.get("method")
    request_id = payload.get("id")

    if method == "initialize":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "open-meteo-weather",
                        "version": "0.1.0",
                    },
                },
            },
            headers={"Mcp-Session-Id": uuid.uuid4().hex},
        )

    if method == "notifications/initialized":
        return Response(status_code=202)

    if method == "tools/list":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {
                            "name": "get_weather",
                            "description": "查询中国城市当前天气（只读）",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                                "required": ["city"],
                            },
                        }
                    ]
                },
            }
        )

    if method == "tools/call":
        params = payload.get("params") or {}
        if params.get("name") != "get_weather":
            return _tool_error(request_id, "unknown tool")
        arguments = params.get("arguments") or {}
        city = str(arguments.get("city") or "").strip()
        if not city:
            return _tool_error(request_id, "city is required")
        try:
            text = await fetch_weather(city)
        except WeatherServiceUnavailable:
            return _tool_error(request_id, "weather service unavailable")
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                },
            }
        )

    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "method not found"},
        }
    )


def _tool_error(request_id: Any, message: str) -> JSONResponse:
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": message}],
                "isError": True,
            },
        }
    )


def _wind_direction(degrees: float) -> str:
    index = round((degrees % 360) / 45) % len(_WIND_DIRECTIONS)
    return _WIND_DIRECTIONS[index]
