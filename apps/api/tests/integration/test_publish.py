import sqlite3
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from hoy_api.publish.service import PublishReport, publish_from_sqlite
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from canarias_events.database import Database, Decision
from canarias_events.models import Event as ImportedEvent
from canarias_events.sources.base import SourceConfig
from tests.conftest import TEST_DATABASE_URL, require_test_database_name
from tests.integration.test_migrations import _alembic_config

NOW = datetime(2026, 10, 16, 3, 0, tzinfo=UTC)
FIESTA_TITLE = "Año Nuevo: «Fiesta» en La Laguna"


@pytest.fixture
def session_factory() -> Generator[sessionmaker[Session], None, None]:
    require_test_database_name(TEST_DATABASE_URL)
    reset = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    with reset.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    reset.dispose()
    command.upgrade(_alembic_config(TEST_DATABASE_URL), "head")
    engine = create_engine(TEST_DATABASE_URL)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _imported(source_id: str, eid: str, title: str, **overrides: object) -> ImportedEvent:
    base: dict[str, object] = {
        "title": title,
        "source_name": f"Fuente {source_id}",
        "source_event_id": eid,
        "source_url": f"https://{source_id}.test/e/{eid}",
        "start_datetime": datetime(2026, 10, 21, 20, 0, tzinfo=UTC),
        "island": "Tenerife",
        "municipality": "Adeje",
        "category": "Conciertos",
        "tags": ["música"],
    }
    base.update(overrides)
    return ImportedEvent.model_validate(base)


def _seed_importer(path: Path, events: list[tuple[str, ImportedEvent]]) -> None:
    with Database(path) as db:
        db.sync_sources(
            [
                SourceConfig(id="a", name="Fuente a", adapter="web", url="https://a.test/"),
                SourceConfig(id="b", name="Fuente b", adapter="web", url="https://b.test/"),
            ]
        )
        for source_id, event in events:
            db.upsert(event, source_id, Decision("new"), NOW)


@pytest.fixture
def importer_path(tmp_path: Path) -> Path:
    path = tmp_path / "importer.sqlite"
    _seed_importer(
        path,
        [
            ("a", _imported("a", "1", FIESTA_TITLE)),
            ("a", _imported("a", "2", "Taller de cerámica", category="Taller")),
            (
                "b",
                _imported(
                    "b",
                    "9",
                    "Concierto con enlace roto",
                    official_event_url="javascript:alert(1)",
                    extra={"status": "cancelled"},
                ),
            ),
        ],
    )
    return path


def _scalar(session_factory: sessionmaker[Session], sql: str) -> object:
    with session_factory() as session:
        return session.execute(text(sql)).scalar_one()


def test_first_publish_inserts_every_dated_event(
    importer_path: Path, session_factory: sessionmaker[Session]
) -> None:
    report = publish_from_sqlite(importer_path, session_factory, NOW)

    assert isinstance(report, PublishReport)
    assert report.inserted == 3
    assert report.updated == 0
    assert _scalar(session_factory, "SELECT count(*) FROM events") == 3
    assert _scalar(session_factory, "SELECT count(*) FROM event_sources") == 3


def test_republish_is_idempotent(
    importer_path: Path, session_factory: sessionmaker[Session]
) -> None:
    publish_from_sqlite(importer_path, session_factory, NOW)
    second = publish_from_sqlite(importer_path, session_factory, NOW)

    assert second.inserted == 0
    assert second.updated == 3
    assert _scalar(session_factory, "SELECT count(*) FROM events") == 3
    assert _scalar(session_factory, "SELECT count(*) FROM event_sources") == 3


def test_slug_is_frozen_after_an_upstream_rename(
    importer_path: Path, session_factory: sessionmaker[Session]
) -> None:
    publish_from_sqlite(importer_path, session_factory, NOW)
    slug_before = _scalar(
        session_factory, "SELECT slug FROM events WHERE title = '" + FIESTA_TITLE + "'"
    )

    with sqlite3.connect(importer_path) as conn:
        conn.execute("UPDATE events SET title = 'Título nuevo' WHERE source_event_id = '1'")
    publish_from_sqlite(importer_path, session_factory, NOW)

    assert (
        _scalar(session_factory, "SELECT slug FROM events WHERE title = 'Título nuevo'")
        == slug_before
    )
    assert str(slug_before).startswith("ano-nuevo-fiesta-en-la-laguna-")


def test_removed_upstream_events_are_marked_stale_not_deleted(
    importer_path: Path, session_factory: sessionmaker[Session]
) -> None:
    publish_from_sqlite(importer_path, session_factory, NOW)
    with sqlite3.connect(importer_path) as conn:
        conn.execute("DELETE FROM event_sources WHERE source_event_id = '2'")
        conn.execute("DELETE FROM events WHERE title = 'Taller de cerámica'")

    report = publish_from_sqlite(importer_path, session_factory, NOW)

    assert report.stale_marked == 1
    assert _scalar(session_factory, "SELECT count(*) FROM events") == 3
    assert _scalar(session_factory, "SELECT count(*) FROM events WHERE stale") == 1


def test_unsafe_urls_and_unknown_fields_stay_null(
    importer_path: Path, session_factory: sessionmaker[Session]
) -> None:
    publish_from_sqlite(importer_path, session_factory, NOW)

    row = _scalar(
        session_factory,
        "SELECT official_event_url FROM events WHERE title = 'Concierto con enlace roto'",
    )
    assert row is None
    assert _scalar(session_factory, "SELECT count(*) FROM events WHERE is_free IS NULL") == 3


def test_status_and_category_are_persisted(
    importer_path: Path, session_factory: sessionmaker[Session]
) -> None:
    publish_from_sqlite(importer_path, session_factory, NOW)

    assert (
        _scalar(
            session_factory,
            "SELECT status FROM events WHERE title = 'Concierto con enlace roto'",
        )
        == "cancelled"
    )
    assert (
        _scalar(session_factory, "SELECT category FROM events WHERE title = 'Taller de cerámica'")
        == "workshops"
    )


def test_search_vector_matches_accent_insensitive_terms(
    importer_path: Path, session_factory: sessionmaker[Session]
) -> None:
    publish_from_sqlite(importer_path, session_factory, NOW)

    matches = _scalar(
        session_factory,
        "SELECT count(*) FROM events WHERE search_tsv @@ to_tsquery('simple', 'ano & fiesta')",
    )
    assert matches == 1
