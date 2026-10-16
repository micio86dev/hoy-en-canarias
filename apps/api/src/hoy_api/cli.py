import json
import signal
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Annotated

import typer
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session, sessionmaker

from hoy_api.db.base import make_engine, make_session_factory
from hoy_api.importer import run_configured_cycle, subprocess_runner
from hoy_api.logging_setup import configure_logging
from hoy_api.publish.service import publish_from_sqlite
from hoy_api.settings import get_settings
from hoy_api.worker import CycleOutcome, run_scheduler

app = typer.Typer(
    help="Hoy en Canarias operations: migrate, publish and run the importer.", no_args_is_help=True
)
API_ROOT = Path(__file__).resolve().parents[2]
SqliteOption = Annotated[Path, typer.Option("--sqlite", help="Importer SQLite database.")]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _session_factory() -> sessionmaker[Session]:
    settings = get_settings()
    return make_session_factory(make_engine(settings.database_url.get_secret_value()))


@app.command("migrate")
def migrate() -> None:
    """Apply all database migrations up to the latest revision."""
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(config, "head")
    typer.echo("migrations applied")


@app.command("publish")
def publish(sqlite: SqliteOption) -> None:
    """Project an importer database into the portal database and print the report."""
    session_factory = _session_factory()
    report = publish_from_sqlite(sqlite, session_factory, _utc_now())
    typer.echo(json.dumps(asdict(report), sort_keys=True))


@app.command("import-now")
def import_now() -> None:
    """Run one importer cycle immediately (same path as the daily schedule)."""
    settings = get_settings()
    configure_logging()
    outcome = run_configured_cycle(
        session_factory=_session_factory(),
        sqlite_path=settings.importer_db_path,
        runner=subprocess_runner(
            settings.importer_command, settings.importer_db_path, settings.importer_timeout_seconds
        ),
        window_days=settings.import_window_days,
        trigger="cli",
        now=_utc_now,
        sleep=time.sleep,
        max_attempts=settings.import_max_attempts,
        backoff_seconds=settings.import_retry_backoff_seconds,
    )
    typer.echo(outcome.status)
    if outcome.status == "failed":
        raise typer.Exit(code=1)


@app.command("worker")
def worker() -> None:
    """Run the daily importer schedule (04:00 Atlantic/Canary) until stopped."""
    settings = get_settings()
    configure_logging()
    session_factory = _session_factory()
    stopping = {"requested": False}

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stopping["requested"] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    def cycle() -> CycleOutcome:
        return run_configured_cycle(
            session_factory=session_factory,
            sqlite_path=settings.importer_db_path,
            runner=subprocess_runner(
                settings.importer_command,
                settings.importer_db_path,
                settings.importer_timeout_seconds,
            ),
            window_days=settings.import_window_days,
            trigger="schedule",
            now=_utc_now,
            sleep=time.sleep,
            max_attempts=settings.import_max_attempts,
            backoff_seconds=settings.import_retry_backoff_seconds,
        )

    run_scheduler(
        cycle=cycle,
        clock=_utc_now,
        sleep=time.sleep,
        should_stop=lambda: stopping["requested"],
    )
