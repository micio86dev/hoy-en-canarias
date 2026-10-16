from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from hoy_api.db.models import ImportRun
from hoy_api.publish.persist import PublishReport
from hoy_api.publish.service import publish_from_sqlite
from hoy_api.scheduling import next_run_at

logger = logging.getLogger("hoy_api.worker")

# Stable key for the single importer-run lock (pg_try_advisory_lock takes a bigint).
IMPORT_LOCK_KEY = 0x48_4F_59_49_4D_50_4F  # "HOYIMPO"
MAX_ERROR_CHARS = 500
_URL_PATTERN = re.compile(r"postgres(?:ql)?(?:\+\w+)?://\S+")
_SECRET_PARAM_PATTERN = re.compile(r"(?i)\b(apikey|api_key|token|secret|password|key)=[^&\s]+")

Trigger = Literal["schedule", "manual", "cli"]
Status = Literal["succeeded", "failed", "skipped_locked"]


@dataclass(frozen=True)
class ImportWindow:
    start: date
    end: date


@dataclass(frozen=True)
class RunnerResult:
    returncode: int
    stderr_tail: str


ImporterRunner = Callable[[ImportWindow], RunnerResult]


@dataclass
class CycleOutcome:
    status: Status
    attempts: int = 0
    report: PublishReport | None = None
    error: str | None = None
    run_id: int | None = None


def sanitize(text: str) -> str:
    """Remove database URLs (which carry passwords) and bound the length of an error."""
    redacted = _URL_PATTERN.sub("<redacted-url>", text)
    redacted = _SECRET_PARAM_PATTERN.sub(lambda m: f"{m.group(1)}=<redacted>", redacted).strip()
    return redacted[:MAX_ERROR_CHARS]


@contextmanager
def acquire_import_lock(session_factory: sessionmaker[Session]) -> Iterator[bool]:
    """Non-blocking, session-level advisory lock: at most one importer cycle runs at a time.

    The lock lives on a dedicated connection, so it stays held until the context exits.
    """
    lock_session = session_factory()
    try:
        acquired = bool(lock_session.scalar(select(func.pg_try_advisory_lock(IMPORT_LOCK_KEY))))
        try:
            yield acquired
        finally:
            if acquired:
                lock_session.execute(select(func.pg_advisory_unlock(IMPORT_LOCK_KEY)))
                lock_session.commit()
    finally:
        lock_session.close()


def _start_run(
    session_factory: sessionmaker[Session], trigger: Trigger, window: ImportWindow, now: datetime
) -> int:
    with session_factory.begin() as session:
        run = ImportRun(
            trigger=trigger,
            status="running",
            window_start=window.start,
            window_end=window.end,
            attempts=0,
            started_at=now,
        )
        session.add(run)
        session.flush()
        return int(run.id)


def _finish_run(
    session_factory: sessionmaker[Session],
    run_id: int,
    *,
    status: Status,
    attempts: int,
    started_at: datetime,
    finished_at: datetime,
    report: PublishReport | None = None,
    error: str | None = None,
) -> None:
    values: dict[str, object] = {
        "status": status,
        "attempts": attempts,
        "finished_at": finished_at,
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "error_summary": error,
    }
    if report is not None:
        values.update(
            events_inserted=report.inserted,
            events_updated=report.updated,
            events_stale=report.stale_marked,
            events_skipped=report.skipped,
        )
    with session_factory.begin() as session:
        session.execute(update(ImportRun).where(ImportRun.id == run_id).values(**values))


def run_import_cycle(
    *,
    session_factory: sessionmaker[Session],
    sqlite_path: Path,
    runner: ImporterRunner,
    window: ImportWindow,
    trigger: Trigger,
    now: Callable[[], datetime],
    sleep: Callable[[float], None],
    max_attempts: int,
    backoff_seconds: float,
) -> CycleOutcome:
    """Run the importer for ``window`` and publish its SQLite output, recording the outcome.

    Retries are bounded. Publishing is idempotent, so a retry after a late failure is safe.
    """
    with acquire_import_lock(session_factory) as acquired:
        started = now()
        if not acquired:
            run_id = _start_run(session_factory, trigger, window, started)
            finished = now()
            _finish_run(
                session_factory,
                run_id,
                status="skipped_locked",
                attempts=0,
                started_at=started,
                finished_at=finished,
            )
            logger.warning("import skipped: another run holds the lock", extra={"run_id": run_id})
            return CycleOutcome(status="skipped_locked", run_id=run_id)

        run_id = _start_run(session_factory, trigger, window, started)
        last_error = "unknown error"
        report: PublishReport | None = None
        attempts = 0
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            try:
                result = runner(window)
                if result.returncode != 0:
                    raise RuntimeError(sanitize(result.stderr_tail) or "importer exited non-zero")
                report = publish_from_sqlite(sqlite_path, session_factory, now())
                break
            except Exception as exc:  # any attempt failure is recorded and may be retried
                last_error = sanitize(str(exc)) or type(exc).__name__
                logger.warning(
                    "import attempt failed",
                    extra={"run_id": run_id, "attempt": attempt, "error": last_error},
                )
                if attempt < max_attempts:
                    sleep(backoff_seconds * attempt)

        finished = now()
        if report is not None:
            _finish_run(
                session_factory,
                run_id,
                status="succeeded",
                attempts=attempts,
                started_at=started,
                finished_at=finished,
                report=report,
            )
            logger.info(
                "import succeeded",
                extra={"run_id": run_id, "attempts": attempts, "inserted": report.inserted},
            )
            return CycleOutcome(status="succeeded", attempts=attempts, report=report, run_id=run_id)

        _finish_run(
            session_factory,
            run_id,
            status="failed",
            attempts=attempts,
            started_at=started,
            finished_at=finished,
            error=last_error,
        )
        logger.error("import failed", extra={"run_id": run_id, "attempts": attempts})
        return CycleOutcome(status="failed", attempts=attempts, error=last_error, run_id=run_id)


def run_scheduler(
    *,
    cycle: Callable[[], CycleOutcome],
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    should_stop: Callable[[], bool],
    max_cycles: int | None = None,
) -> int:
    """Run ``cycle`` once per day at 04:00 Atlantic/Canary. Returns how many cycles ran."""
    completed = 0
    while not should_stop():
        now = clock()
        remaining = (next_run_at(now) - now).total_seconds()
        while remaining > 0 and not should_stop():
            step = min(remaining, 30.0)
            sleep(step)
            remaining -= step
        if should_stop():
            break
        cycle()
        completed += 1
        if max_cycles is not None and completed >= max_cycles:
            break
    return completed
