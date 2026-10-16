from datetime import timedelta
from pathlib import Path

import pytest
from hoy_api.worker import (
    CycleOutcome,
    ImporterRunner,
    ImportWindow,
    RunnerResult,
    acquire_import_lock,
    run_import_cycle,
)
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.seed import FIXED_NOW, demo_events, seed_importer

WINDOW = ImportWindow(start=FIXED_NOW.date(), end=FIXED_NOW.date() + timedelta(days=60))


@pytest.fixture
def sqlite_path(tmp_path: Path) -> Path:
    path = tmp_path / "importer.sqlite"
    seed_importer(path, demo_events())
    return path


def _ok(_: ImportWindow) -> RunnerResult:
    return RunnerResult(returncode=0, stderr_tail="")


def _cycle(
    session_factory: sessionmaker[Session],
    sqlite_path: Path,
    runner: ImporterRunner,
    sleeps: list[float],
) -> CycleOutcome:
    return run_import_cycle(
        session_factory=session_factory,
        sqlite_path=sqlite_path,
        runner=runner,
        window=WINDOW,
        trigger="manual",
        now=lambda: FIXED_NOW,
        sleep=sleeps.append,
        max_attempts=2,
        backoff_seconds=5.0,
    )


def _last_run(session_factory: sessionmaker[Session]) -> dict[str, object]:
    with session_factory() as session:
        row = (
            session.execute(
                text(
                    "SELECT status, attempts, events_inserted, error_summary "
                    "FROM import_runs ORDER BY id DESC LIMIT 1"
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


def test_successful_cycle_publishes_and_records_the_run(
    session_factory: sessionmaker[Session], sqlite_path: Path
) -> None:
    sleeps: list[float] = []

    outcome = _cycle(session_factory, sqlite_path, _ok, sleeps)

    assert outcome.status == "succeeded"
    run = _last_run(session_factory)
    assert run["status"] == "succeeded"
    assert run["attempts"] == 1
    assert run["events_inserted"] == 5
    assert sleeps == []


def test_failing_importer_retries_then_records_a_sanitized_failure(
    session_factory: sessionmaker[Session], sqlite_path: Path
) -> None:
    def failing(_: ImportWindow) -> RunnerResult:
        return RunnerResult(
            returncode=1,
            stderr_tail="boom postgresql+psycopg://hoy:hunter2@db:5432/hoy at /secret/path",
        )

    sleeps: list[float] = []
    outcome = _cycle(session_factory, sqlite_path, failing, sleeps)

    assert outcome.status == "failed"
    run = _last_run(session_factory)
    assert run["status"] == "failed"
    assert run["attempts"] == 2
    assert sleeps == [5.0]  # one backoff between the two attempts
    assert "hunter2" not in str(run["error_summary"])
    assert "<redacted-url>" in str(run["error_summary"])


def test_timeout_counts_as_a_failed_attempt(
    session_factory: sessionmaker[Session], sqlite_path: Path
) -> None:
    def timing_out(_: ImportWindow) -> RunnerResult:
        raise TimeoutError("importer exceeded 900 s")

    outcome = _cycle(session_factory, sqlite_path, timing_out, [])

    assert outcome.status == "failed"
    assert _last_run(session_factory)["attempts"] == 2


def test_overlapping_run_is_skipped_while_the_lock_is_held(
    session_factory: sessionmaker[Session], sqlite_path: Path
) -> None:
    calls: list[ImportWindow] = []

    def recording(window: ImportWindow) -> RunnerResult:
        calls.append(window)
        return RunnerResult(returncode=0, stderr_tail="")

    with acquire_import_lock(session_factory) as held:
        assert held is True
        outcome = _cycle(session_factory, sqlite_path, recording, [])

    assert outcome.status == "skipped_locked"
    assert calls == []
    assert _last_run(session_factory)["status"] == "skipped_locked"


def test_lock_is_released_after_the_cycle(
    session_factory: sessionmaker[Session], sqlite_path: Path
) -> None:
    _cycle(session_factory, sqlite_path, _ok, [])

    with acquire_import_lock(session_factory) as held:
        assert held is True


def test_secret_query_parameters_are_masked_in_stored_errors() -> None:
    from hoy_api.worker import sanitize

    raw = "GET https://app.ticketmaster.com/discovery/v2/events.json?apikey=ABC123&size=20 failed"

    cleaned = sanitize(raw)

    assert "ABC123" not in cleaned
    assert "apikey=<redacted>" in cleaned
    assert "size=20" in cleaned
