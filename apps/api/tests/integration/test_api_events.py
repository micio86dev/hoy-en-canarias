from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hoy_api.main import create_app
from hoy_api.publish.service import publish_from_sqlite
from hoy_api.settings import Settings
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
    *,
    rate_limit: int = 600,
    clock: Callable[[], datetime] = lambda: FIXED_NOW,
) -> TestClient:
    settings = Settings(
        database_url="postgresql+psycopg://unused:unused@localhost:1/unused",
        rate_limit_per_minute=rate_limit,
        cors_origins=["https://hoyencanarias.es"],
    )
    app = create_app(settings=settings, session_factory=session_factory, clock=clock)
    return TestClient(app)


@pytest.fixture
def client(published: sessionmaker[Session]) -> Iterator[TestClient]:
    with _client(published) as test_client:
        yield test_client


def _titles(response_body: dict[str, Any]) -> list[str]:
    return [item["title"] for item in response_body["items"]]


def test_default_listing_returns_upcoming_events_in_date_order(client: TestClient) -> None:
    body = client.get("/api/v1/events").json()

    assert body["total"] == 4
    assert _titles(body) == [
        "Fiesta de la Caña Dulce",
        "Mercado de artesanía",
        "Concierto de jazz en el puerto",
        "Concierto de rock en Adeje",
    ]


def test_text_search_is_accent_insensitive(client: TestClient) -> None:
    body = client.get("/api/v1/events", params={"q": "cana dulce"}).json()

    assert _titles(body) == ["Fiesta de la Caña Dulce"]


def test_text_search_matches_title_words(client: TestClient) -> None:
    body = client.get("/api/v1/events", params={"q": "concierto"}).json()

    assert body["total"] == 2


def test_island_filter(client: TestClient) -> None:
    body = client.get("/api/v1/events", params={"island": "Tenerife"}).json()

    assert body["total"] == 2
    assert all(item["island"] == "Tenerife" for item in body["items"])


def test_free_only_filter(client: TestClient) -> None:
    body = client.get("/api/v1/events", params={"free": "true"}).json()

    assert _titles(body) == ["Concierto de jazz en el puerto"]


def test_weekend_preset_resolves_to_saturday_and_sunday(client: TestClient) -> None:
    body = client.get("/api/v1/events", params={"preset": "weekend"}).json()

    assert _titles(body) == ["Fiesta de la Caña Dulce"]


def test_explicit_date_range(client: TestClient) -> None:
    body = client.get("/api/v1/events", params={"from": "2026-10-19", "to": "2026-10-19"}).json()

    assert _titles(body) == ["Mercado de artesanía"]


def test_distance_sort_and_radius(client: TestClient) -> None:
    near = client.get(
        "/api/v1/events",
        params={"lat": 27.9, "lon": -15.4, "sort": "distance", "radius_km": 50},
    ).json()

    assert _titles(near) == ["Fiesta de la Caña Dulce"]
    assert near["items"][0]["distance_km"] == pytest.approx(0.0, abs=0.01)


def test_distance_sort_orders_by_proximity(client: TestClient) -> None:
    body = client.get(
        "/api/v1/events", params={"lat": 27.9, "lon": -15.4, "sort": "distance"}
    ).json()

    # Events without coordinates sort last, by date.
    assert _titles(body) == [
        "Fiesta de la Caña Dulce",
        "Concierto de jazz en el puerto",
        "Mercado de artesanía",
        "Concierto de rock en Adeje",
    ]


def test_pagination_reports_total_and_pages(client: TestClient) -> None:
    body = client.get("/api/v1/events", params={"limit": 1, "page": 2}).json()

    assert body["total"] == 4
    assert body["page"] == 2
    assert _titles(body) == ["Mercado de artesanía"]


@pytest.mark.parametrize(
    "params",
    [
        {"lat": 28.1},  # latitude without longitude
        {"from": "2026-10-20", "to": "2026-10-10"},  # inverted range
        {"sort": "distance"},  # distance sort without coordinates
        {"limit": 51},  # above the page cap
        {"q": "x" * 121},  # query too long
        {"island": "Atlantis"},  # not a Canary island
        {"lat": 95, "lon": 0},  # coordinate out of range
    ],
)
def test_invalid_queries_are_rejected_with_error_body(
    client: TestClient, params: dict[str, Any]
) -> None:
    response = client.get("/api/v1/events", params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_detail_by_slug_returns_sanitized_description_and_provenance(client: TestClient) -> None:
    listing = client.get("/api/v1/events", params={"q": "jazz"}).json()
    slug = listing["items"][0]["slug"]

    detail = client.get(f"/api/v1/events/{slug}").json()

    assert detail["description"] == "Jazz al atardecer."
    assert detail["category"] == "music"
    assert detail["sources"] == [
        {
            "source_name": "Fuente a",
            "source_url": "https://a.test/e/1",
            "official_url": None,
        }
    ]


def test_unknown_slug_is_a_not_found_error_body(client: TestClient) -> None:
    response = client.get("/api/v1/events/no-existe-000000")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Event not found"}}


def test_related_events_share_category_or_island(client: TestClient) -> None:
    slug = client.get("/api/v1/events", params={"q": "jazz"}).json()["items"][0]["slug"]

    related = client.get(f"/api/v1/events/{slug}/related").json()

    assert [item["title"] for item in related["items"]] == ["Concierto de rock en Adeje"]


def test_map_returns_only_events_with_coordinates_inside_bbox(client: TestClient) -> None:
    body = client.get("/api/v1/events/map", params={"bbox": "-17,27,-15,29"}).json()

    assert {point["title"] for point in body["items"]} == {
        "Concierto de jazz en el puerto",
        "Fiesta de la Caña Dulce",
    }
    assert body["truncated"] is False


def test_map_rejects_malformed_bbox(client: TestClient) -> None:
    response = client.get("/api/v1/events/map", params={"bbox": "-15,29,-17,27"})

    assert response.status_code == 422


def test_islands_report_upcoming_counts(client: TestClient) -> None:
    body = client.get("/api/v1/islands").json()

    counts = {item["key"]: item["count"] for item in body["items"]}
    assert counts["Tenerife"] == 2
    assert counts["Gran Canaria"] == 1
    assert counts["La Palma"] == 0
    assert len(counts) == 8


def test_categories_report_upcoming_counts(client: TestClient) -> None:
    body = client.get("/api/v1/categories").json()

    counts = {item["key"]: item["count"] for item in body["items"]}
    assert counts["music"] == 2
    assert counts["festivals"] == 1


def test_municipalities_are_filterable_by_island(client: TestClient) -> None:
    body = client.get("/api/v1/municipalities", params={"island": "Tenerife"}).json()

    assert body["items"] == [{"key": "Adeje", "count": 2}]


def test_security_headers_are_set(client: TestClient) -> None:
    response = client.get("/api/v1/events")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_cors_allows_only_configured_origins(client: TestClient) -> None:
    allowed = client.get("/api/v1/events", headers={"Origin": "https://hoyencanarias.es"})
    denied = client.get("/api/v1/events", headers={"Origin": "https://evil.test"})

    assert allowed.headers.get("access-control-allow-origin") == "https://hoyencanarias.es"
    assert "access-control-allow-origin" not in denied.headers


def test_health_and_readiness(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}


def test_rate_limit_returns_429_with_retry_after(published: sessionmaker[Session]) -> None:
    with _client(published, rate_limit=2) as limited:
        statuses = [limited.get("/api/v1/events").status_code for _ in range(3)]
        response = limited.get("/api/v1/events")

    assert statuses == [200, 200, 429]
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert response.json()["error"]["code"] == "rate_limited"
