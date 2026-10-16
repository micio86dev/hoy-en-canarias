from datetime import UTC, datetime

from hoy_api.scheduling import import_window, next_run_at


def test_next_run_is_today_when_before_0400_local() -> None:
    # 02:00 UTC on 16 Oct is 03:00 in the Canaries (WEST): the 04:00 run is still ahead today.
    now = datetime(2026, 10, 16, 2, 0, tzinfo=UTC)

    assert next_run_at(now) == datetime(2026, 10, 16, 3, 0, tzinfo=UTC)


def test_next_run_moves_to_tomorrow_after_0400_local() -> None:
    now = datetime(2026, 10, 16, 3, 0, tzinfo=UTC)  # 04:00 WEST: today's run is due now

    assert next_run_at(now) == datetime(2026, 10, 17, 3, 0, tzinfo=UTC)


def test_next_run_keeps_0400_local_across_the_october_dst_change() -> None:
    # Canary Islands leave WEST (UTC+1) on 25 Oct 2026; 04:00 local is then 04:00 UTC.
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)

    assert next_run_at(now) == datetime(2026, 10, 25, 4, 0, tzinfo=UTC)
    assert next_run_at(datetime(2026, 10, 25, 4, 0, tzinfo=UTC)) == datetime(
        2026, 10, 26, 4, 0, tzinfo=UTC
    )


def test_import_window_starts_today_and_spans_the_configured_days() -> None:
    start, end = import_window(datetime(2026, 10, 16, 3, 0, tzinfo=UTC), days=60)

    assert start.isoformat() == "2026-10-16"
    assert end.isoformat() == "2026-12-15"
