from datetime import UTC, datetime

import pytest
from hoy_api.publish.categories import map_category
from hoy_api.publish.projection import SourceLink, project_event, safe_http_url
from hoy_api.publish.slug import make_slug

from canarias_events.models import Event as ImportedEvent

IMPORTER_ID = "3f9a1c2d4e5f60718293a4b5c6d7e8f9"


@pytest.mark.parametrize(
    ("title", "expected_prefix"),
    [
        ("Año Nuevo: «Fiesta» en La Laguna", "ano-nuevo-fiesta-en-la-laguna"),
        ("¡Concierto de Jazz!", "concierto-de-jazz"),
        ("   ", "evento"),
    ],
)
def test_slug_is_ascii_and_suffixed_with_id(title: str, expected_prefix: str) -> None:
    slug = make_slug(title, IMPORTER_ID)

    assert slug == f"{expected_prefix}-3f9a1c"


def test_slug_truncates_long_titles_at_a_word_boundary() -> None:
    slug = make_slug("palabra " * 40, IMPORTER_ID)

    base = slug.rsplit("-", 1)[0]
    assert len(base) <= 60
    assert not base.endswith("-")


@pytest.mark.parametrize(
    ("raw", "tags", "expected"),
    [
        ("Conciertos", [], "music"),
        (None, ["Fiesta de la Caña Dulce"], "festivals"),
        ("Mercadillo navideño", [], "markets"),
        ("Ruta de senderismo", [], "nature"),
        ("Taller de cerámica", [], "workshops"),
        ("Partido de fútbol", [], "sports"),
        ("Teatro infantil", [], "family"),
        ("Teatro", [], "culture"),
        ("Fiesta en el club", [], "nightlife"),
        ("Asamblea de vecinos", [], "other"),
    ],
)
def test_category_mapping_uses_word_prefixes(
    raw: str | None, tags: list[str], expected: str
) -> None:
    assert map_category(raw, tags) == expected


def test_safe_http_url_accepts_only_http_and_https() -> None:
    assert safe_http_url("https://club.test/e/1") == "https://club.test/e/1"
    assert safe_http_url("http://club.test/e/1") == "http://club.test/e/1"
    assert safe_http_url("javascript:alert(1)") is None
    assert safe_http_url("data:text/html,hi") is None
    assert safe_http_url("https://club.test/a b") is None
    assert safe_http_url("  ") is None
    assert safe_http_url(None) is None


def _imported(**overrides: object) -> ImportedEvent:
    base: dict[str, object] = {
        "id": IMPORTER_ID,
        "title": "Concierto en el puerto",
        "source_name": "Fuente",
        "start_datetime": datetime(2026, 10, 21, 20, 0, tzinfo=UTC),
        "island": "Tenerife",
        "municipality": "Adeje",
        "category": "Conciertos",
        "tags": ["música"],
    }
    base.update(overrides)
    return ImportedEvent.model_validate(base)


def test_projection_assumes_canary_timezone_for_naive_datetimes() -> None:
    event = _imported(start_datetime=datetime(2026, 10, 21, 20, 0))

    projected = project_event(event, [])

    assert projected.start_at is not None
    offset = projected.start_at.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 3600  # WEST, UTC+1 in October


def test_projection_keeps_unknown_fields_null() -> None:
    event = _imported(venue_name=None, price_min=None, is_free=None, image_url=None)

    projected = project_event(event, [])

    assert projected.venue_name is None
    assert projected.is_free is None
    assert projected.image_url is None
    assert projected.price_min is None


def test_projection_strips_html_from_description() -> None:
    event = _imported(description="<p>Hola <b>mundo</b></p>")

    assert project_event(event, []).description == "Hola mundo"


def test_projection_rejects_unknown_island_and_status() -> None:
    event = _imported(island="Atlantis", extra={"status": "bogus"})

    projected = project_event(event, [])

    assert projected.island is None
    assert projected.status == "unknown"


def test_projection_keeps_known_status() -> None:
    event = _imported(extra={"status": "cancelled"})

    assert project_event(event, []).status == "cancelled"


def test_projection_maps_category_and_keeps_raw_value() -> None:
    projected = project_event(_imported(), [])

    assert projected.category == "music"
    assert projected.category_raw == "Conciertos"


def test_projection_carries_source_links() -> None:
    link = SourceLink(
        source_name="Hola",
        source_event_id="evt-1",
        source_url="https://hola.test/evt-1",
        official_url="https://club.test/e/1",
    )

    projected = project_event(_imported(), [link])

    assert projected.sources == (link,)
    assert projected.sources[0].official_url == "https://club.test/e/1"
