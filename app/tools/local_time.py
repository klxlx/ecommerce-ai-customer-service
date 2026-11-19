"""Local-time facts for the chitchat agent."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def get_local_time(now: datetime | None = None) -> dict[str, str]:
    """Return the current Shanghai local time as a structured tool fact."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local = current.astimezone(SHANGHAI_TZ)
    weekday = _WEEKDAYS[local.weekday()]
    date_text = local.strftime("%Y-%m-%d")
    time_text = local.strftime("%H:%M:%S")
    return {
        "timezone": "Asia/Shanghai",
        "date": date_text,
        "time": time_text,
        "weekday": weekday,
        "text": (
            f"{local:%Y年%m月%d日} {time_text}"
            f"（{weekday}，Asia/Shanghai）"
        ),
    }
