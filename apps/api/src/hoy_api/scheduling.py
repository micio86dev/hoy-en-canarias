from datetime import UTC, date, datetime, time, timedelta

from hoy_api.time_windows import CANARY_TZ, local_today

DAILY_RUN_LOCAL = time(4, 0)


def next_run_at(now: datetime) -> datetime:
    """The next 04:00 Atlantic/Canary instant strictly after ``now``, returned in UTC.

    Each candidate is built from the local calendar date, so the schedule follows
    daylight-saving changes instead of drifting by an hour twice a year.
    """
    local_now = now.astimezone(CANARY_TZ)
    candidate = datetime.combine(local_now.date(), DAILY_RUN_LOCAL, tzinfo=CANARY_TZ)
    if candidate <= local_now:
        candidate = datetime.combine(
            local_now.date() + timedelta(days=1), DAILY_RUN_LOCAL, tzinfo=CANARY_TZ
        )
    return candidate.astimezone(UTC)


def import_window(now: datetime, days: int) -> tuple[date, date]:
    """Inclusive local-day window from today to ``days`` days ahead."""
    start = local_today(now)
    return start, start + timedelta(days=days)
