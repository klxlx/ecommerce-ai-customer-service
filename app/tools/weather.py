"""Read-only weather MCP client and lightweight intent helpers."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.core.config import Settings

_WEATHER_PATTERN = re.compile(
    r"(天气|气温|温度|下雨|降雨|下雪|晴天|阴天|冷不冷|热不热|空气质量)"
)
_LOCAL_TIME_PATTERN = re.compile(
    r"(?:"
    r"(?:现在|当前|本地)(?:是)?(?:什么|几)(?:时间|时候)"
    r"|几点(?:了)?"
    r"|(?:现在|当前|本地)(?:的)?(?:时间|日期)"
    r"|今天(?:是)?(?:几号|几月几号|星期几|周几)"
    r"|^(?:请问|问一下)?(?:时间|日期|星期几|周几)"
    r"(?:呢|是多少|是什么)?[？?。！!]*$"
    r")"
)
_CITY_BEFORE_WEATHER = re.compile(
    r"(?P<city>[\u4e00-\u9fff]{1,12}?)"
    r"(?:今天|明天|后天|未来几天|这周|本周|周末)?"
    r"(?:的)?(?:天气|气温|温度|下雨|降雨|下雪|冷不冷|热不热|空气质量)"
)
_KNOWN_CITIES = (
    "北京",
    "上海",
    "天津",
    "重庆",
    "广州",
    "深圳",
    "杭州",
    "南京",
    "苏州",
    "成都",
    "武汉",
    "西安",
    "长沙",
    "郑州",
    "青岛",
    "厦门",
    "宁波",
    "合肥",
    "济南",
    "福州",
    "昆明",
    "大连",
    "沈阳",
    "哈尔滨",
    "长春",
    "石家庄",
    "太原",
    "南昌",
    "贵阳",
    "南宁",
    "海口",
    "兰州",
    "西宁",
    "银川",
    "乌鲁木齐",
    "拉萨",
    "呼和浩特",
)
_CITY_PREFIXES = (
    "帮我查一下",
    "帮我查询",
    "帮我看看",
    "我想知道",
    "查一下",
    "查询一下",
    "查询",
    "看看",
    "请问",
    "今天",
    "明天",
    "后天",
)


class WeatherUnavailable(RuntimeError):
    """Raised when weather facts cannot be obtained safely."""


def is_weather_query(text: str) -> bool:
    """Return whether a message asks for weather facts."""
    return bool(_WEATHER_PATTERN.search(text))


def is_local_time_query(text: str) -> bool:
    """Return whether a message asks for the current local time/date."""
    if re.search(r"订单.*(?:什么时候|几点|多久).*到", text):
        return False
    return bool(_LOCAL_TIME_PATTERN.search(text))


def extract_weather_city(text: str) -> str:
    """Extract a common Chinese city name from a weather question."""
    for city in _KNOWN_CITIES:
        if city in text:
            return city

    match = _CITY_BEFORE_WEATHER.search(text)
    if not match:
        return ""
    city = match.group("city").strip()
    for prefix in _CITY_PREFIXES:
        if city.startswith(prefix):
            city = city[len(prefix) :]
    city = re.sub(r"(?:的|市|地区|本地)$", "", city).strip()
    if city in {"今天", "明天", "后天", "现在", "这里", "本地"}:
        return ""
    if len(city) < 2 or len(city) > 8:
        return ""
    return city


class MCPWeatherClient:
    """Minimal Streamable HTTP MCP client for a read-only weather tool."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._enabled = settings.weather_mcp_enabled
        self._url = settings.weather_mcp_url.strip()
        self._tool = settings.weather_mcp_tool.strip() or "get_weather"
        self._timeout = settings.weather_mcp_timeout_seconds
        self._auth_token = settings.weather_mcp_auth_token.strip()
        self._client = client

    async def query(self, city: str) -> str:
        """Return weather text for ``city`` or raise ``WeatherUnavailable``."""
        if not self._enabled or not self._url:
            raise WeatherUnavailable("weather MCP is not configured")
        if not city.strip():
            raise WeatherUnavailable("city is required")

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
        )
        session_id = ""
        try:
            init_response = await self._post(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "ecommerce-customer-service",
                            "version": "0.1.0",
                        },
                    },
                },
            )
            init_payload = _decode_rpc_response(init_response)
            _raise_for_rpc_error(init_payload)
            session_id = init_response.headers.get("Mcp-Session-Id", "")

            initialized_response = await self._post(
                client,
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                },
                session_id=session_id,
            )
            initialized_response.raise_for_status()

            tool_response = await self._post(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": self._tool,
                        "arguments": {"city": city.strip()},
                    },
                },
                session_id=session_id,
            )
            payload = _decode_rpc_response(tool_response)
            _raise_for_rpc_error(payload)
            result = payload.get("result") or {}
            if result.get("isError"):
                raise WeatherUnavailable("weather MCP returned an error")
            content = result.get("content") or []
            texts = [
                str(item.get("text") or "").strip()
                for item in content
                if isinstance(item, dict)
                and item.get("type") == "text"
                and str(item.get("text") or "").strip()
            ]
            if not texts:
                raise WeatherUnavailable("weather MCP returned no text")
            return "\n".join(texts)
        except WeatherUnavailable:
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise WeatherUnavailable("weather MCP request failed") from exc
        finally:
            if owns_client:
                await client.aclose()

    async def _post(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        *,
        session_id: str = "",
    ) -> httpx.Response:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        response = await client.post(
            self._url,
            json=payload,
            headers=headers,
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response


def _decode_rpc_response(response: httpx.Response) -> dict[str, Any]:
    if not response.content:
        return {}
    try:
        payload = response.json()
    except json.JSONDecodeError:
        payload = _decode_sse(response.text)
    if not isinstance(payload, dict):
        raise ValueError("MCP response must be a JSON object")
    return payload


def _decode_sse(text: str) -> dict[str, Any]:
    data_lines = [
        line[5:].strip()
        for line in text.splitlines()
        if line.startswith("data:")
    ]
    for data in reversed(data_lines):
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("MCP response contained no JSON data")


def _raise_for_rpc_error(payload: dict[str, Any]) -> None:
    error = payload.get("error")
    if error:
        raise WeatherUnavailable("weather MCP returned a JSON-RPC error")
