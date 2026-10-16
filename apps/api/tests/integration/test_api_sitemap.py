from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hoy_api.main import create_app
from hoy_api.publish.service import publish_from_sqlite
from hoy_api.settings import Settings
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.seed import FIXED_NOW, demo_events, imported, seed_importer


@pytest.fixture
def client(tmp_path: Path, session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    path = tmp_path / "importer.sqlite"
    seed_importer(path, demo_events())
    publish_from_sqlite(path, session_factory, FIXED_NOW)
    app = create_app(
        settings=Settings(database_url="postgresql+psycopg://unused:unused@localhost:1/unused"),
        session_factory=session_factory,
        clock=lambda: FIXED_NOW,
    )
    with TestClient(app) as test_client:
        yield test_client


def test_sitemap_data_lists_upcoming_events_only(client: TestClient) -> None:
    body = client.get("/api/v1/sitemap-data").json()

    slugs = {item["slug"] for item in body["items"]}
    assert len(slugs) == 4  # the past event is not indexable
    assert all(item["lastmod"] for item in body["items"])


def test_sitemap_data_excludes_stale_events(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    path = tmp_path / "importer.sqlite"
    seed_importer(path, [("a", imported("a", "1", "Visible evento"))])
    publish_from_sqlite(path, session_factory, FIXED_NOW)
    with session_factory.begin() as session:
        session.execute(text("UPDATE events SET stale = true"))
    app = create_app(
        settings=Settings(database_url="postgresql+psycopg://unused:unused@localhost:1/unused"),
        session_factory=session_factory,
        clock=lambda: FIXED_NOW,
    )

    with TestClient(app) as stale_client:
        body = stale_client.get("/api/v1/sitemap-data").json()

    assert body["items"] == []
