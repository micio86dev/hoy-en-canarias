import json
from pathlib import Path

import pytest
from hoy_api.cli import app
from hoy_api.settings import get_settings
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from tests.conftest import TEST_DATABASE_URL
from tests.integration.seed import demo_events, seed_importer

runner = CliRunner()


def test_help_lists_operational_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("migrate", "publish", "import-now", "worker"):
        assert command in result.output


def test_publish_command_projects_an_importer_database(
    tmp_path: Path, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    sqlite_path = tmp_path / "importer.sqlite"
    seed_importer(sqlite_path, demo_events())
    monkeypatch.setenv("HOY_DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()  # settings are cached; re-read the test environment

    result = runner.invoke(app, ["publish", "--sqlite", str(sqlite_path)])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["inserted"] == 5
    with session_factory() as session:
        assert session.execute(text("SELECT count(*) FROM events")).scalar_one() == 5
