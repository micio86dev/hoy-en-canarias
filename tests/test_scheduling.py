"""Per-source frequency, failure backoff and the cool-down after non-temporary statuses."""

from __future__ import annotations

from datetime import timedelta

import pytest

from canarias_events.crawl_settings import CrawlSettings, parse_frequency
from canarias_events.database import SourceRun
from canarias_events.scheduling import skip_reason
from tests.conftest import NOW
from tests.test_database import make_cfg


def run_at(hours_ago: float, status: str = "failed", source_status: str | None = None) -> SourceRun:
    return SourceRun(
        run_id=1,
        source_id="s",
        started_at=(NOW - timedelta(hours=hours_ago)).isoformat(),
        status=status,
        source_status=source_status,
        events_found=0,
        events_in_window=0,
        duration_s=1.0,
        error=None,
        strategy=None,
        extraction_ok=None,
        listing_hash=None,
        anomalies=[],
    )


def last_success(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


@pytest.mark.parametrize(
    ("text", "hours"),
    [("daily", 24), ("weekly", 168), ("hourly", 1), ("6h", 6), ("12H", 12), ("2d", 48)],
)
def test_parse_frequency(text: str, hours: int) -> None:
    assert parse_frequency(text) == timedelta(hours=hours)


@pytest.mark.parametrize("text", ["", "often", "0h", "-3h", "1.5h", "h"])
def test_parse_frequency_rejects_garbage(text: str) -> None:
    with pytest.raises(ValueError, match="frequency"):
        parse_frequency(text)


def test_source_config_validates_the_frequency() -> None:
    assert make_cfg(frequency="weekly").frequency == "weekly"
    assert make_cfg().frequency is None
    with pytest.raises(ValueError, match="frequency"):
        make_cfg(frequency="sometimes")


def reason(
    cfg_frequency: str | None, runs: list[SourceRun], success: str | None, **kw: object
) -> str | None:
    settings = CrawlSettings(**kw)
    return skip_reason(make_cfg(frequency=cfg_frequency), runs, success, NOW, settings)


def test_no_frequency_means_always_due() -> None:
    assert reason(None, [], last_success(0.1)) is None


def test_recent_success_skips_until_the_frequency_has_passed() -> None:
    assert "frequency daily" in (reason("daily", [], last_success(3)) or "")
    assert reason("daily", [], last_success(25)) is None
    assert reason("weekly", [], None) is None  # never succeeded: due


def test_three_temporary_failures_start_an_exponential_backoff() -> None:
    fails = [run_at(2), run_at(30), run_at(60)]
    assert "backoff" in (reason(None, fails, None) or "")  # 3 failures: wait 6h, 2h passed
    assert reason(None, [run_at(7), run_at(30), run_at(60)], None) is None  # 7h > 6h
    four = [run_at(7), run_at(30), run_at(60), run_at(90)]
    assert "backoff" in (reason(None, four, None) or "")  # 4 failures: wait 12h
    assert reason(None, [run_at(13), *four[1:]], None) is None


def test_backoff_is_capped() -> None:
    long = [run_at(200 + i) for i in range(10)]
    assert reason(None, long, None) is None  # cap 168h < 200h since the last failure
    capped = reason(None, [run_at(100), *long[1:]], None, backoff_max_hours=500)
    assert "backoff" in (capped or "")


def test_below_the_threshold_nothing_is_skipped() -> None:
    assert reason(None, [run_at(1), run_at(2)], None) is None
    assert reason(None, [run_at(1), run_at(2)], None, failure_threshold=2) is not None


def test_a_success_in_between_resets_the_streak() -> None:
    runs = [run_at(1), run_at(2), run_at(3, status="succeeded"), run_at(4), run_at(5)]
    assert reason(None, runs, None) is None


@pytest.mark.parametrize("status", ["robots_disallowed", "needs_manual_review", "partial"])
def test_non_temporary_statuses_cool_down_instead_of_backing_off(status: str) -> None:
    source_status = "blocked" if status == "partial" else status
    runs = [run_at(1, status=status, source_status=source_status)]
    assert "cool-down" in (reason(None, runs, None) or "")
    old = [run_at(80, status=status, source_status=source_status)]
    assert reason(None, old, None) is None  # 72h default cool-down passed


def test_blocked_failures_are_not_temporary_failures() -> None:
    runs = [run_at(i, source_status="blocked") for i in (80, 90, 100)]
    assert reason(None, runs, None) is None  # past the cool-down, and no backoff streak
    assert "cool-down" in (reason(None, [run_at(1, source_status="blocked")], None) or "")


def test_a_rate_limit_is_a_temporary_failure_not_a_cool_down() -> None:
    assert reason(None, [run_at(1, source_status="rate_limited")], None) is None


def test_repeated_rate_limits_back_off_exponentially() -> None:
    runs = [run_at(h, source_status="rate_limited") for h in (1, 10, 20)]
    assert "backoff" in (reason(None, runs, None) or "")
    assert "backoff" not in (
        reason(None, [run_at(h, source_status="blocked") for h in (1, 10, 20)], None) or ""
    )


def test_a_baseline_reset_run_does_not_hide_the_backoff_or_cool_down() -> None:
    synthetic = run_at(0.5, status="succeeded")
    object.__setattr__(synthetic, "anomalies", ["baseline_reset"])
    fails = [run_at(2), run_at(30), run_at(60)]
    assert "backoff" in (reason(None, [synthetic, *fails], None) or "")
    blocked = [run_at(1, status="robots_disallowed", source_status="robots_disallowed")]
    assert "cool-down" in (reason(None, [synthetic, *blocked], None) or "")


def test_a_baseline_reset_run_does_not_defer_the_frequency_schedule() -> None:
    synthetic = run_at(0.5, status="succeeded")
    object.__setattr__(synthetic, "anomalies", ["baseline_reset"])
    assert reason("daily", [synthetic], last_success(30)) is None
