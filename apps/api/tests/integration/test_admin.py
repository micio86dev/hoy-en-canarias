from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hoy_api.main import create_app
from hoy_api.settings import Settings
from hoy_api.worker import RunnerResult
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.seed import FIXED_NOW, demo_events, seed_importer

TOKEN = "s3cret-admin-token"


@pytest.fixture
def sqlite_path(tmp_path: Path) -> Path:
    path = tmp_path / "importer.sqlite"
    seed_importer(path, demo_events())
    return path


def _client(
    session_factory: sessionmaker[Session], sqlite_path: Path, admin_token: str | None
) -> TestClient:
    settings = Settings(
        database_url="postgresql+psycopg://unused:unused@localhost:1/unused",
        admin_token=admin_token,
        importer_db_path=sqlite_path,
        import_retry_backoff_seconds=0,
    )
    app = create_app(settings=settings, session_factory=session_factory, clock=lambda: FIXED_NOW)
    app.state.run_importer = lambda _window: RunnerResult(returncode=0, stderr_tail="")
    return TestClient(app)


def test_admin_import_is_disabled_without_a_configured_token(
    session_factory: sessionmaker[Session], sqlite_path: Path
) -> None:
    with _client(session_factory, sqlite_path, admin_token=None) as client:
        response = client.post("/api/v1/admin/import")

    assert response.status_code == 404


@pytest.mark.parametrize("header", [None, "Bearer wrong", "Basic czNjcmV0"])
def test_admin_import_rejects_missing_or_wrong_credentials(
    session_factory: sessionmaker[Session], sqlite_path: Path, header: str | None
) -> None:
    headers = {"Authorization": header} if header else {}
    with _client(session_factory, sqlite_path, admin_token=TOKEN) as client:
        response = client.post("/api/v1/admin/import", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_admin_import_with_token_runs_a_recorded_manual_cycle(
    session_factory: sessionmaker[Session], sqlite_path: Path
) -> None:
    with _client(session_factory, sqlite_path, admin_token=TOKEN) as client:
        response = client.post("/api/v1/admin/import", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 202
    with session_factory() as session:
        row = session.execute(
            text("SELECT trigger, status FROM import_runs ORDER BY id DESC LIMIT 1")
        ).one()
    assert (row.trigger, row.status) == ("manual", "succeeded")
