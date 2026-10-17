from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from hoy_api.db.models import EventAttendance
from hoy_api.main import create_app
from hoy_api.publish.service import publish_from_sqlite
from hoy_api.settings import Settings
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.seed import FIXED_NOW, demo_events, seed_importer


@pytest.fixture
def published(tmp_path: Path, session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    path = tmp_path / "importer.sqlite"
    seed_importer(path, demo_events())
    publish_from_sqlite(path, session_factory, FIXED_NOW)
    return session_factory


def _client(
    session_factory: sessionmaker[Session],
    clock: Callable[[], datetime] = lambda: FIXED_NOW,
) -> TestClient:
    settings = Settings(
        database_url="postgresql+psycopg://unused:unused@localhost:1/unused",
        rate_limit_per_minute=600,
        cors_origins=["https://hoyencanarias.es"],
    )
    app = create_app(settings=settings, session_factory=session_factory, clock=clock)
    return TestClient(app)


@pytest.fixture
def client(published: sessionmaker[Session]) -> Iterator[TestClient]:
    with _client(published) as test_client:
        yield test_client


@pytest.fixture
def slug(client: TestClient) -> str:
    body = client.get("/api/v1/events", params={"limit": 1}).json()
    return str(body["items"][0]["slug"])


def _attendance(
    client: TestClient, slug: str, guest: UUID, method: str = "GET"
) -> dict[str, object]:
    response = client.request(
        method, f"/api/v1/events/{slug}/attendance", headers={"X-Guest-Id": str(guest)}
    )
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_new_event_has_no_attendees_for_a_guest(client: TestClient, slug: str) -> None:
    assert _attendance(client, slug, uuid4()) == {"attendees": 0, "attending": False}


def test_joining_counts_each_guest_once(client: TestClient, slug: str) -> None:
    first, second = uuid4(), uuid4()

    assert _attendance(client, slug, first, "POST") == {"attendees": 1, "attending": True}
    assert _attendance(client, slug, first, "POST") == {"attendees": 1, "attending": True}
    assert _attendance(client, slug, second, "POST") == {"attendees": 2, "attending": True}


def test_leaving_removes_only_that_guest(client: TestClient, slug: str) -> None:
    first, second = uuid4(), uuid4()
    _attendance(client, slug, first, "POST")
    _attendance(client, slug, second, "POST")

    assert _attendance(client, slug, first, "DELETE") == {"attendees": 1, "attending": False}
    assert _attendance(client, slug, second) == {"attendees": 1, "attending": True}


def test_event_detail_exposes_the_attendee_count(client: TestClient, slug: str) -> None:
    _attendance(client, slug, uuid4(), "POST")

    body = client.get(f"/api/v1/events/{slug}").json()

    assert body["attendee_count"] == 1


def test_missing_or_malformed_guest_header_is_rejected(client: TestClient, slug: str) -> None:
    assert client.get(f"/api/v1/events/{slug}/attendance").status_code == 422
    bad = client.post(f"/api/v1/events/{slug}/attendance", headers={"X-Guest-Id": "not-a-uuid"})
    assert bad.status_code == 422


def test_unknown_event_is_not_found(client: TestClient) -> None:
    response = client.post(
        "/api/v1/events/no-existe-000000/attendance", headers={"X-Guest-Id": str(uuid4())}
    )
    assert response.status_code == 404


def test_guest_identifier_is_stored_only_as_a_hash(
    client: TestClient, slug: str, published: sessionmaker[Session]
) -> None:
    guest = uuid4()
    _attendance(client, slug, guest, "POST")

    with published() as session:
        stored = session.scalars(select(EventAttendance.guest_hash)).all()

    assert len(stored) == 1
    assert str(guest) not in stored[0]
    assert len(stored[0]) == 64
