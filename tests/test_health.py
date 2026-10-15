"""Source run history and the per-source health summary."""

from __future__ import annotations

from datetime import timedelta

from canarias_events.database import Database, SourceRun
from canarias_events.health import summarize
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
