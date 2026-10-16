"""Prepare the end-to-end database: reset the schema, migrate, and load the demo catalogue.

Refuses to run against any database whose name does not end in ``_test``, because it drops
the public schema. Invoked by the Playwright global setup.
"""

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from hoy_api.db.base import make_session_factory
from hoy_api.devdata import write_importer_database
from hoy_api.publish.service import publish_from_sqlite
from hoy_api.time_windows import local_today
from sqlalchemy import create_engine, text

API_ROOT = Path(__file__).resolve().parents[1]


def _require_test_database(url: str) -> None:
    database = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not database.endswith("_test"):
        raise SystemExit(f"refusing to reset non-test database {database!r}")


def main() -> None:
    url = os.environ["HOY_DATABASE_URL"]
    _require_test_database(url)

    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    now = datetime.now(UTC)
    app_engine = create_engine(url)
    session_factory = make_session_factory(app_engine)
    with tempfile.TemporaryDirectory() as scratch:
        sqlite_path = Path(scratch) / "importer.sqlite"
        write_importer_database(sqlite_path, local_today(now), now)
        report = publish_from_sqlite(sqlite_path, session_factory, now)
    app_engine.dispose()
    print(f"e2e database ready: {report.inserted} events published")


if __name__ == "__main__":
    main()
