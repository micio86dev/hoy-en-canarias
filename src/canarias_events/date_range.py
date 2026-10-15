"""Time window helpers: the "next week" (Monday-Sunday) in Atlantic/Canary time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Atlantic/Canary")


def to_local(value: datetime) -> datetime:
    """Interpret naive datetimes as Atlantic/Canary; convert aware ones."""
    if value.tzinfo is None:
        return value.replace(tzinfo=TZ)
    return value.astimezone(TZ)


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=TZ)


@dataclass(frozen=True)
class DateWindow:
    """Half-open window [start, end) of aware datetimes in Atlantic/Canary."""

    start: datetime
    end: datetime

    @property
    def start_date(self) -> date:
        return self.start.date()

    @property
    def end_date_inclusive(self) -> date:
        return (self.end - timedelta(microseconds=1)).date()

    def overlaps(
        self,
        start: datetime | date,
        end: datetime | date | None,
        all_day: bool,
    ) -> bool:
        """Return True when the event [start, end) intersects the window.

        All-day events use local dates; an inclusive date-only end covers that whole day.
        A missing end (or end <= start) makes the event a point in time.
        """
        if not isinstance(start, datetime) or all_day:
            s_day = start.date() if isinstance(start, datetime) else start
            s = _midnight(s_day)
            if end is None:
                e = _midnight(s_day + timedelta(days=1))
            else:
                e_day = end.date() if isinstance(end, datetime) else end
                e = _midnight(max(e_day, s_day) + timedelta(days=1))
        else:
            s = to_local(start)
            if end is None:
                e = s
            elif isinstance(end, datetime):
                e = to_local(end)
            else:
                e = _midnight(end + timedelta(days=1))
        if e <= s:  # zero-length / inverted: a point in time
            return self.start <= s < self.end
        return s < self.end and e > self.start


def explicit_window(from_date: date, to_date: date) -> DateWindow:
    """Window from inclusive dates: [from 00:00, to+1 00:00)."""
    if to_date < from_date:
        raise ValueError(f"to_date {to_date} is before from_date {from_date}")
    return DateWindow(_midnight(from_date), _midnight(to_date + timedelta(days=1)))


def next_week(now: datetime | None = None) -> DateWindow:
    """Next Monday 00:00 strictly after today (local) through the following Monday."""
    local_now = to_local(now) if now is not None else datetime.now(TZ)
    today = local_now.date()
    days_ahead = 7 - today.weekday()  # Monday -> 7, Sunday -> 1
    monday = today + timedelta(days=days_ahead)
    return DateWindow(_midnight(monday), _midnight(monday + timedelta(days=7)))
