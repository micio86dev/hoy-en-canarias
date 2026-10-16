from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from hoy_api.main import create_app
from hoy_api.moderation.jev import Verdict
from hoy_api.settings import Settings
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.seed import FIXED_NOW

TOKEN = "admin-test-token"


class FakeModerator:
    def __init__(self, probability: float | None) -> None:
        self.probability = probability
        self.calls: list[str] = []

    def spam_probability(self, text: str) -> Verdict:
        self.calls.append(text)
        return Verdict(self.probability)


def _client(
    session_factory: sessionmaker[Session], probability: float | None
) -> tuple[TestClient, FakeModerator]:
    moderator = FakeModerator(probability)
    settings = Settings(
        database_url="postgresql+psycopg://unused:unused@localhost:1/unused",
        admin_token=TOKEN,
        rate_limit_per_minute=600,
    )
    app = create_app(
        settings=settings,
        session_factory=session_factory,
        clock=lambda: FIXED_NOW,
        moderator=moderator,
    )
    return TestClient(app), moderator


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[tuple[TestClient, FakeModerator]]:
    test_client, moderator = _client(session_factory, probability=0.01)
    with test_client:
        yield test_client, moderator


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Mercadillo de Garachico",
        "description": "Artesanía local cada sábado en el puerto.",
        "start_date": "2026-10-24",
        "island": "Tenerife",
        "municipality": "Garachico",
        "url": "https://garachico.test/mercadillo",
        "honeypot": "",
    }
    base.update(overrides)
    return base


def test_clean_submission_is_moderated_then_published(
    client: tuple[TestClient, FakeModerator],
) -> None:
    api, moderator = client

    response = api.post("/api/v1/submissions", json=_payload())

    assert response.status_code == 202
    assert response.json()["status"] == "received"
    assert "reason" not in response.json()  # visitors never learn why a submission was held
    assert moderator.calls and "[correo]" not in moderator.calls[0]
    listed = api.get("/api/v1/events", params={"q": "Garachico"}).json()
    assert [item["title"] for item in listed["items"]] == ["Mercadillo de Garachico"]


def test_contact_details_are_removed_before_moderation(
    client: tuple[TestClient, FakeModerator],
) -> None:
    api, moderator = client

    api.post(
        "/api/v1/submissions",
        json=_payload(description="Reservas en ana@ejemplo.com o +34 922 123 456."),
    )

    assert "ana@ejemplo.com" not in moderator.calls[0]
    assert "922 123 456" not in moderator.calls[0]


def test_honeypot_is_rejected_silently_and_never_published(
    client: tuple[TestClient, FakeModerator],
) -> None:
    api, moderator = client

    response = api.post("/api/v1/submissions", json=_payload(honeypot="https://spam.test"))

    assert response.status_code == 202
    assert moderator.calls == []  # rules reject before any model call
    assert api.get("/api/v1/events", params={"q": "Garachico"}).json()["total"] == 0


def test_likely_spam_is_rejected_by_the_model(session_factory: sessionmaker[Session]) -> None:
    api, _ = _client(session_factory, probability=0.97)
    with api:
        api.post("/api/v1/submissions", json=_payload(title="Casino gratis gana dinero"))
        assert api.get("/api/v1/events", params={"q": "Casino"}).json()["total"] == 0


def test_without_a_model_key_every_submission_waits_for_a_person(
    session_factory: sessionmaker[Session],
) -> None:
    api, _ = _client(session_factory, probability=None)
    with api:
        api.post("/api/v1/submissions", json=_payload())
        assert api.get("/api/v1/events", params={"q": "Garachico"}).json()["total"] == 0
        pending = api.get(
            "/api/v1/admin/submissions",
            params={"status": "pending"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        ).json()
        assert len(pending["items"]) == 1


def test_admin_can_approve_a_pending_submission(session_factory: sessionmaker[Session]) -> None:
    api, _ = _client(session_factory, probability=None)
    with api:
        api.post("/api/v1/submissions", json=_payload())
        headers = {"Authorization": f"Bearer {TOKEN}"}
        pending = api.get(
            "/api/v1/admin/submissions", params={"status": "pending"}, headers=headers
        ).json()
        submission_id = pending["items"][0]["id"]

        approved = api.post(f"/api/v1/admin/submissions/{submission_id}/approve", headers=headers)

        assert approved.status_code == 200
        assert api.get("/api/v1/events", params={"q": "Garachico"}).json()["total"] == 1


def test_admin_routes_need_the_token(client: tuple[TestClient, FakeModerator]) -> None:
    api, _ = client

    assert api.get("/api/v1/admin/submissions").status_code == 401


def test_invalid_submissions_are_refused_with_the_error_shape(
    client: tuple[TestClient, FakeModerator],
) -> None:
    api, _ = client

    response = api.post("/api/v1/submissions", json=_payload(title="x", island="Atlantis"))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_approved_submissions_survive_importer_runs(session_factory: sessionmaker[Session]) -> None:
    from dataclasses import replace

    from hoy_api.moderation.service import to_projected
    from hoy_api.publish.persist import publish_events

    api, _ = _client(session_factory, probability=0.01)
    with api:
        api.post("/api/v1/submissions", json=_payload())
    assert api.get("/api/v1/events", params={"q": "Garachico"}).json()["total"] == 1

    # A real importer export that lacks the submission runs the stale check; the submission
    # is not part of that export and must stay visible.
    other = replace(
        to_projected(uuid4(), _payload()),
        importer_id="importer:otro",
        slug="otro-evento",
        title="Otro evento de prueba",
        municipality="Otro lugar",
        description="Sin relación",
    )
    with session_factory.begin() as session:
        publish_events(session, [other], FIXED_NOW)
    assert api.get("/api/v1/events", params={"q": "Garachico"}).json()["total"] == 1
