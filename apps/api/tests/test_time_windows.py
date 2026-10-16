from datetime import UTC, date, datetime

import pytest
from hoy_api.time_windows import local_today, preset_window


def test_local_today_uses_canary_calendar_day() -> None:
    # 23:30 UTC on 15 October is already 00:30 on 16 October in the Canary Islands (WEST).
    assert local_today(datetime(2026, 10, 15, 23, 30, tzinfo=UTC)) == date(2026, 10, 16)


@pytest.mark.parametrize(
    ("today", "preset", "expected"),
    [
        (date(2026, 10, 16), "today", (date(2026, 10, 16), date(2026, 10, 16))),
        (date(2026, 10, 16), "tomorrow", (date(2026, 10, 17), date(2026, 10, 17))),
        # Friday -> the coming Saturday and Sunday.
        (date(2026, 10, 16), "weekend", (date(2026, 10, 17), date(2026, 10, 18))),
        # Saturday -> today and Sunday.
        (date(2026, 10, 17), "weekend", (date(2026, 10, 17), date(2026, 10, 18))),
        # Sunday -> only today.
        (date(2026, 10, 18), "weekend", (date(2026, 10, 18), date(2026, 10, 18))),
    ],
)
def test_presets_resolve_against_local_today(
    today: date, preset: str, expected: tuple[date, date]
) -> None:
    assert preset_window(preset, today) == expected  # type: ignore[arg-type]
