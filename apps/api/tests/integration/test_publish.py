import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hoy_api.publish.service import PublishReport, publish_from_sqlite
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.seed import imported, seed_importer

NOW = datetime(2026, 10, 16, 3, 0, tzinfo=UTC)
FIESTA_TITLE = "Año Nuevo: «Fiesta» en La Laguna"


@pytest.fixture
def importer_path(tmp_path: Path) -> Path:
    path = tmp_path / "importer.sqlite"
    seed_importer(
        path,
        [
            ("a", imported("a", "1", FIESTA_TITLE)),
            ("a", imported("a", "2", "Taller de cerámica", category="Taller")),
            (
                "b",
                imported(
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


def test_empty_importer_export_does_not_mark_everything_stale(
    importer_path: Path, session_factory: sessionmaker[Session]
) -> None:
    publish_from_sqlite(importer_path, session_factory, NOW)
    empty_path = importer_path.with_name("empty.sqlite")
    seed_importer(empty_path, [])

    report = publish_from_sqlite(empty_path, session_factory, NOW)

    assert report.stale_marked == 0
    assert _scalar(session_factory, "SELECT count(*) FROM events WHERE stale") == 0
