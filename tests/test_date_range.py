from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from canarias_events.date_range import TZ, DateWindow, explicit_window, next_week

MADRID = ZoneInfo("Europe/Madrid")


def local(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=TZ)


def test_tz_is_atlantic_canary() -> None:
    assert str(TZ) == "Atlantic/Canary"


def test_next_week_from_thursday() -> None:
    w = next_week(local(2026, 10, 15, 10))
    assert w.start == local(2026, 10, 19)
    assert w.end == local(2026, 10, 26)
    assert w.start_date == date(2026, 10, 19)
    assert w.end_date_inclusive == date(2026, 10, 25)


def test_next_week_on_monday_is_following_monday() -> None:
    w = next_week(local(2026, 10, 19, 0, 0))
    assert w.start == local(2026, 10, 26)
    assert w.end == local(2026, 11, 2)


def test_next_week_on_sunday() -> None:
    w = next_week(local(2026, 10, 18, 23, 59))
    assert w.start == local(2026, 10, 19)


def test_month_rollover() -> None:
    w = next_week(local(2026, 10, 29))
    assert w.start == local(2026, 11, 2)
    assert w.end == local(2026, 11, 9)


def test_year_rollover() -> None:
    w = next_week(local(2026, 12, 30))
    assert w.start == local(2027, 1, 4)
    assert w.end_date_inclusive == date(2027, 1, 10)


def test_utc_input_uses_local_date() -> None:
    # 2026-10-18 is a Sunday. Canary is UTC+1 in summer time (until 2026-10-25),
    # so 23:30 UTC Sunday is already 00:30 Monday local: next Monday is the 26th.
    now = datetime(2026, 10, 18, 23, 30, tzinfo=UTC)
    w = next_week(now)
    assert w.start == local(2026, 10, 26)


def test_utc_input_still_sunday_locally() -> None:
    # 22:30 UTC Sunday is 23:30 Sunday local (UTC+1) -> next Monday is the 19th.
    now = datetime(2026, 10, 18, 22, 30, tzinfo=UTC)
    assert next_week(now).start == local(2026, 10, 19)


def test_naive_now_is_interpreted_as_local() -> None:
    assert next_week(datetime(2026, 10, 15, 9, 0)).start == local(2026, 10, 19)


def test_default_now_returns_monday_window() -> None:
    w = next_week()
    assert w.start.weekday() == 0
    assert w.end_date_inclusive.weekday() == 6
    assert (w.end_date_inclusive - w.start_date).days == 6


def test_explicit_window_inclusive() -> None:
    w = explicit_window(date(2026, 10, 19), date(2026, 10, 25))
    assert w.start == local(2026, 10, 19)
    assert w.end == local(2026, 10, 26)
    assert w.end_date_inclusive == date(2026, 10, 25)


def test_explicit_window_single_day() -> None:
    w = explicit_window(date(2026, 10, 19), date(2026, 10, 19))
    assert w.end == local(2026, 10, 20)


def test_explicit_window_rejects_inverted() -> None:
    with pytest.raises(ValueError):
        explicit_window(date(2026, 10, 20), date(2026, 10, 19))


@pytest.fixture
def window() -> DateWindow:
    return explicit_window(date(2026, 10, 19), date(2026, 10, 25))


def test_event_inside_overlaps(window: DateWindow) -> None:
    assert window.overlaps(local(2026, 10, 20, 20), local(2026, 10, 20, 22), False)


def test_multi_day_event_starting_before_overlaps(window: DateWindow) -> None:
    assert window.overlaps(local(2026, 10, 17, 10), local(2026, 10, 20, 10), False)


def test_event_ending_exactly_at_window_start_does_not_overlap(window: DateWindow) -> None:
    assert not window.overlaps(local(2026, 10, 18, 20), local(2026, 10, 19, 0, 0), False)


def test_event_starting_exactly_at_window_end_does_not_overlap(window: DateWindow) -> None:
    assert not window.overlaps(local(2026, 10, 26, 0, 0), local(2026, 10, 26, 2), False)


def test_event_before_and_after_do_not_overlap(window: DateWindow) -> None:
    assert not window.overlaps(local(2026, 10, 10), local(2026, 10, 11), False)
    assert not window.overlaps(local(2026, 11, 1), local(2026, 11, 2), False)


def test_all_day_event_on_sunday_overlaps(window: DateWindow) -> None:
    assert window.overlaps(date(2026, 10, 25), date(2026, 10, 25), True)


def test_all_day_event_on_previous_sunday_does_not_overlap(window: DateWindow) -> None:
    assert not window.overlaps(date(2026, 10, 18), date(2026, 10, 18), True)


def test_all_day_event_on_following_monday_does_not_overlap(window: DateWindow) -> None:
    assert not window.overlaps(date(2026, 10, 26), None, True)


def test_all_day_without_end_is_single_day(window: DateWindow) -> None:
    assert window.overlaps(date(2026, 10, 19), None, True)


def test_point_event_without_end(window: DateWindow) -> None:
    assert window.overlaps(local(2026, 10, 19, 0, 0), None, False)
    assert not window.overlaps(local(2026, 10, 26, 0, 0), None, False)
    assert not window.overlaps(local(2026, 10, 18, 23, 59), None, False)


def test_zero_length_event_at_window_start_overlaps(window: DateWindow) -> None:
    t = local(2026, 10, 19, 0, 0)
    assert window.overlaps(t, t, False)


def test_other_timezone_is_converted(window: DateWindow) -> None:
    # DST ended on 2026-10-25, so on the 26th Madrid is UTC+1 and Canary is UTC+0.
    # 2026-10-26 00:30 Madrid == 2026-10-25 23:30 Canary: inside window.
    s = datetime(2026, 10, 26, 0, 30, tzinfo=MADRID)
    assert window.overlaps(s, None, False)
    # 2026-10-26 01:30 Madrid == 2026-10-26 00:30 Canary: outside.
    s2 = datetime(2026, 10, 26, 1, 30, tzinfo=MADRID)
    assert not window.overlaps(s2, None, False)


def test_naive_datetimes_are_canary_local(window: DateWindow) -> None:
    assert window.overlaps(datetime(2026, 10, 25, 23, 0), None, False)
    assert not window.overlaps(datetime(2026, 10, 26, 0, 0), None, False)


def test_window_is_frozen_dataclass() -> None:
    w = DateWindow(local(2026, 10, 19), local(2026, 10, 26))
    with pytest.raises(AttributeError):
        w.start = local(2026, 10, 20)  # type: ignore[misc]
