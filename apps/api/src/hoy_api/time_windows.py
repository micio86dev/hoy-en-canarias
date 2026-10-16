from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

CANARY_TZ_NAME = "Atlantic/Canary"
CANARY_TZ = ZoneInfo(CANARY_TZ_NAME)

Preset = Literal["today", "tomorrow", "weekend"]


def local_today(now: datetime) -> date:
    """The calendar day in the Canary Islands for an aware instant."""
    return now.astimezone(CANARY_TZ).date()


def preset_window(preset: Preset, today: date) -> tuple[date, date]:
    """Resolve a date shortcut to an inclusive (from, to) window."""
    if preset == "today":
        return today, today
    if preset == "tomorrow":
        tomorrow = today + timedelta(days=1)
        return tomorrow, tomorrow
    # weekend: Saturday and Sunday. Sunday alone is "today"; mid-week points to the coming Saturday.
    weekday = today.weekday()  # Monday == 0 ... Sunday == 6
    if weekday == 6:
        return today, today
    if weekday == 5:
        return today, today + timedelta(days=1)
    saturday = today + timedelta(days=5 - weekday)
    return saturday, saturday + timedelta(days=1)
