import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from hoy_api.scheduling import import_window
from hoy_api.worker import (
    CycleOutcome,
    ImporterRunner,
    ImportWindow,
    RunnerResult,
    Trigger,
    run_import_cycle,
)

STDERR_TAIL_LINES = 5


def build_import_argv(command: str, window: ImportWindow, sqlite_path: Path) -> list[str]:
    """The importer is run as its own process: its code and CLI stay unchanged."""
    return [
        command,
        "import-events",
        "--from",
        window.start.isoformat(),
        "--to",
        window.end.isoformat(),
        "--db",
        str(sqlite_path),
        "--no-ai",
    ]


def format_stderr_tail(stderr: str) -> str:
    lines = [line for line in stderr.splitlines() if line.strip()]
    return "\n".join(lines[-STDERR_TAIL_LINES:])


def window_from(instant: datetime, days: int) -> ImportWindow:
    """Today (Canary calendar) through ``days`` ahead, inclusive."""
    start, end = import_window(instant, days)
    return ImportWindow(start=start, end=end)


def subprocess_runner(command: str, sqlite_path: Path, timeout_seconds: int) -> ImporterRunner:
    def run(window: ImportWindow) -> RunnerResult:
        completed = subprocess.run(
            build_import_argv(command, window, sqlite_path),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return RunnerResult(
            returncode=completed.returncode, stderr_tail=format_stderr_tail(completed.stderr)
        )

    return run


def run_configured_cycle(
    *,
    session_factory: sessionmaker[Session],
    sqlite_path: Path,
    runner: ImporterRunner,
    window_days: int,
    trigger: Trigger,
    now: Callable[[], datetime],
    sleep: Callable[[float], None],
    max_attempts: int,
    backoff_seconds: float,
) -> CycleOutcome:
    window = window_from(now(), window_days)
    return run_import_cycle(
        session_factory=session_factory,
        sqlite_path=sqlite_path,
        runner=runner,
        window=window,
        trigger=trigger,
        now=now,
        sleep=sleep,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
    )
