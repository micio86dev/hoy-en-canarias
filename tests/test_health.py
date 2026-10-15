"""Source run history and the per-source health summary."""

from __future__ import annotations

from datetime import timedelta

from canarias_events.crawl_settings import CrawlSettings
from canarias_events.database import Database, SourceRun
from canarias_events.health import detect_anomalies, is_blocking, summarize
from tests.conftest import NOW


def record(
    db: Database,
    source: str,
    n: int,
    status: str = "succeeded",
    found: int = 10,
    *,
    error: str | None = None,
    strategy: str | None = "blocks",
    extraction_ok: bool | None = True,
    anomalies: list[str] | None = None,
) -> None:
    db.record_source_run(
        n,
        source,
        NOW + timedelta(hours=n),
        status=status,
        source_status="ok",
        events_found=found,
        events_in_window=found,
        duration_s=1.5,
        error=error,
        strategy=strategy,
        extraction_ok=extraction_ok,
        listing_hash="h1",
        anomalies=anomalies or [],
    )


def test_history_roundtrip_is_newest_first() -> None:
    with Database(":memory:") as db:
        record(db, "a", 1, found=3, strategy="jsonld", anomalies=["x"])
        record(db, "a", 2, status="failed", found=0, error="boom", extraction_ok=None)
        record(db, "b", 3)
        runs = db.recent_source_runs("a", limit=5)
    assert [r.run_id for r in runs] == [2, 1]
    assert runs[1] == SourceRun(
        run_id=1,
        source_id="a",
        started_at=(NOW + timedelta(hours=1)).isoformat(),
        status="succeeded",
        source_status="ok",
        events_found=3,
        events_in_window=3,
        duration_s=1.5,
        error=None,
        strategy="jsonld",
        extraction_ok=True,
        listing_hash="h1",
        anomalies=["x"],
    )
    assert runs[0].extraction_ok is None and runs[0].error == "boom"


def test_history_limit() -> None:
    with Database(":memory:") as db:
        for n in range(1, 8):
            record(db, "a", n)
        assert [r.run_id for r in db.recent_source_runs("a", limit=3)] == [7, 6, 5]


def test_summary_of_a_source_without_history() -> None:
    assert summarize([]) == {
        "runs_considered": 0,
        "last_status": None,
        "last_events_found": None,
        "last_success_at": None,
        "error_rate": None,
        "consecutive_failures": 0,
        "last_anomalies": [],
    }


def test_summary_counts_failures_and_error_rate() -> None:
    with Database(":memory:") as db:
        record(db, "a", 1, found=12)
        record(db, "a", 2, found=8)
        record(db, "a", 3, status="failed", found=0, error="x")
        record(db, "a", 4, status="robots_disallowed", found=0)
        runs = db.recent_source_runs("a", limit=10)
    summary = summarize(runs)
    assert summary["runs_considered"] == 4
    assert summary["last_status"] == "robots_disallowed"
    assert summary["consecutive_failures"] == 2
    assert summary["error_rate"] == 0.5
    assert summary["last_events_found"] == 8  # of the last successful run
    assert summary["last_success_at"] == (NOW + timedelta(hours=2)).isoformat()


def test_summary_resets_the_streak_on_success() -> None:
    with Database(":memory:") as db:
        record(db, "a", 1, status="failed", found=0)
        record(db, "a", 2)
        runs = db.recent_source_runs("a", limit=10)
    assert summarize(runs)["consecutive_failures"] == 0


# --------------------------------------------------------------------------- anomaly detection


def history(*counts: int, status: str = "succeeded", strategy: str = "blocks") -> list[SourceRun]:
    """Runs newest first, one per count."""
    with Database(":memory:") as db:
        for n, found in enumerate(reversed(counts), start=1):
            record(db, "a", n, status=status, found=found, strategy=strategy)
        return db.recent_source_runs("a", limit=50)


def detect(
    runs: list[SourceRun],
    found: int,
    *,
    ok: bool | None = True,
    strategy: str | None = "blocks",
    listing_hash: str | None = "h1",
    source_status: str = "ok",
    settings: CrawlSettings | None = None,
) -> list[str]:
    return detect_anomalies(
        runs,
        events_found=found,
        extraction_ok=ok,
        strategy=strategy,
        listing_hash=listing_hash,
        source_status=source_status,
        settings=settings or CrawlSettings(),
    )


def test_no_history_means_no_anomaly() -> None:
    assert detect([], 0) == []


def test_zero_events_after_a_source_that_had_events_is_flagged() -> None:
    assert detect(history(12, 10), 0) == ["empty_after_events"]


def test_zero_events_with_a_changed_listing_is_a_layout_change() -> None:
    assert detect(history(12, 10), 0, listing_hash="other") == ["layout_changed"]


def test_zero_events_with_failed_extraction_is_flagged() -> None:
    assert detect(history(12), 0, ok=False) == ["extraction_failed"]


def test_a_declared_empty_agenda_is_not_an_anomaly() -> None:
    assert detect(history(12, 10), 0, strategy="empty-marker") == []


def test_zero_events_from_a_source_that_was_always_empty_is_normal() -> None:
    assert detect(history(0, 0, 0), 0) == []


def test_count_drop_beyond_the_ratio_vs_the_median() -> None:
    runs = history(20, 22, 18, 20, 21)  # median 20 -> threshold 6 at ratio 0.7
    assert detect(runs, 5) == ["count_drop"]
    assert detect(runs, 6) == []
    assert detect(runs, 12) == []


def test_count_drop_ratio_is_configurable() -> None:
    runs = history(20, 20, 20)
    assert detect(runs, 12, settings=CrawlSettings(anomaly_drop_ratio=0.3)) == ["count_drop"]
    assert detect(runs, 12) == []


def test_count_drop_needs_enough_history_and_a_meaningful_baseline() -> None:
    assert detect(history(20, 20), 1) == []  # two runs are not a baseline
    assert detect(history(4, 4, 4), 0) == ["empty_after_events"]  # zero rule still applies
    assert detect(history(4, 4, 4), 1) == []  # tiny sources fluctuate


def test_anomalous_and_failed_runs_do_not_shape_the_baseline() -> None:
    good = history(20, 20, 20)
    bad = history(1, 1, 1, status="anomalous")
    assert detect(bad + good, 5) == ["count_drop"]


def test_strategy_change_is_reported_but_is_not_blocking() -> None:
    codes = detect(history(10, 10, 10), 10, strategy="render+blocks")
    assert codes == ["strategy_changed"]
    assert not is_blocking(codes)
    assert is_blocking(["count_drop", "strategy_changed"])


def test_blocked_or_disallowed_sources_have_no_content_anomaly() -> None:
    for status in ("blocked", "robots_disallowed"):
        assert detect(history(12, 10), 0, source_status=status) == []
