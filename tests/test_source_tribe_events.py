from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import httpx
import pytest
import respx

from canarias_events.date_range import TZ
from canarias_events.http import HttpError, InvalidPayload
from canarias_events.sources.base import FetchContext, SourceConfig
from canarias_events.sources.tribe_events import TribeEventsAdapter
from tests.conftest import fixture_json

MakeCtx = Callable[..., FetchContext]


def cfg(host: str = "www.candelaria.es", **overrides: Any) -> SourceConfig:
    data: dict[str, Any] = {
        "id": "demo",
        "name": "Demo Tribe",
        "adapter": "tribe_events",
        "url": f"https://{host}/wp-json/tribe/events/v1/events",
        "islands": ["Tenerife"],
        "license": None,
    }
    data.update(overrides)
    return SourceConfig.model_validate(data)


def mock_page(
    host: str, payload: Any, path: str = "/wp-json/tribe/events/v1/events"
) -> respx.Route:
    return respx.get(host=host, path=path).mock(return_value=httpx.Response(200, json=payload))


# --------------------------------------------------------------------------- mapping


@respx.mock
def test_maps_candelaria_events(make_ctx: MakeCtx) -> None:
    payload = fixture_json("tribe_candelaria.json")
    payload.pop("next_rest_url")
    mock_page("www.candelaria.es", payload)
    config = cfg(municipality="Candelaria")

    result = TribeEventsAdapter(config).fetch(make_ctx())

    assert result.records_received == 3
    assert result.pages == 1
    assert result.discarded == []
    first = result.events[0]
    assert first.title == "Encuentro por el Día Internacional de la Mujer Rural"
    # Declared timezone is Europe/London (BST == WEST): 17:00 local stays 17:00 in Canarias.
    assert first.start_datetime == datetime(2026, 10, 15, 17, 0, tzinfo=TZ)
    assert first.end_datetime == datetime(2026, 10, 15, 17, 0, tzinfo=TZ)
    assert first.timezone == "Atlantic/Canary"
    assert first.all_day is False
    assert first.source_name == "Demo Tribe"
    assert first.source_url == config.url
    assert first.official_event_url == (
        "https://www.candelaria.es/event/encuentro-por-el-dia-internacional-de-la-mujer-rural/"
    )
    assert first.source_event_id == "47164"
    assert first.source_type == "tribe_events"
    assert first.category == "Desarrollo rural y pesquero"
    assert first.tags == ["Noticias"]
    assert first.image_url == (
        "https://www.candelaria.es/wp-content/uploads/Agenda-Octubre-2026-Pantallas-23-1.jpg"
    )
    assert first.municipality == "Candelaria"
    assert first.island == "Tenerife"
    assert first.price_min is None and first.is_free is None
    assert first.description and "<" not in first.description
    assert first.raw_data["id"] == 47164


@respx.mock
def test_maps_venue_and_all_day(make_ctx: MakeCtx) -> None:
    payload = fixture_json("tribe_granadilla.json")
    payload.pop("next_rest_url")
    mock_page("www.granadilladeabona.org", payload)
    config = cfg("www.granadilladeabona.org", municipality="Granadilla de Abona")

    events = TribeEventsAdapter(config).fetch(make_ctx()).events

    cheese = events[0]
    assert cheese.venue_name == "Instalaciones Deportivas de Los Hinojeros"
    assert cheese.address == "Dr. Antonio Pérez Díaz"
    assert cheese.postal_code == "38600"
    assert cheese.municipality == "Granadilla de Abona"
    assert cheese.start_datetime == datetime(2026, 10, 17, 10, 0, tzinfo=TZ)
    assert cheese.latitude is None and cheese.longitude is None
    # "San isidro" is a locality, not a municipality: fall back to the configured one.
    talk = events[2]
    assert talk.all_day is True
    assert talk.start_date == date(2026, 10, 31) and talk.end_date == date(2026, 10, 31)
    assert talk.start_datetime is None
    assert talk.municipality == "Granadilla de Abona"
    assert talk.extra["venue_city"] == "San isidro"


@respx.mock
def test_non_iana_timezone_is_treated_as_canary_wall_clock(make_ctx: MakeCtx) -> None:
    payload = fixture_json("deportestenerife_tribe.json")
    payload.pop("next_rest_url")
    mock_page("deportestenerife.es", payload)

    events = TribeEventsAdapter(cfg("deportestenerife.es")).fetch(make_ctx()).events

    judo = events[0]
    assert payload["events"][0]["timezone"] == "UTC+0"
    assert judo.start_datetime == datetime(2026, 10, 17, 8, 30, tzinfo=TZ)
    assert judo.venue_name == "Pabellón Municipal Rosendo Alonso Tapia"
    assert judo.municipality == "Candelaria"
    assert judo.island == "Tenerife"
    # Venue "Tenerife" has no city: municipality stays unknown, island comes from config.
    assert events[2].municipality is None
    assert events[2].island == "Tenerife"


@respx.mock
def test_declared_iana_timezone_is_converted(make_ctx: MakeCtx) -> None:
    payload = fixture_json("tribe_candelaria.json")
    payload.pop("next_rest_url")
    payload["events"][0]["timezone"] = "Europe/Madrid"  # 17:00 Madrid == 16:00 Canary (summer)
    mock_page("www.candelaria.es", payload)

    first = TribeEventsAdapter(cfg()).fetch(make_ctx()).events[0]

    assert first.start_datetime == datetime(2026, 10, 15, 16, 0, tzinfo=TZ)


@respx.mock
def test_price_organizer_geo_and_website(make_ctx: MakeCtx) -> None:
    payload = fixture_json("tribe_candelaria.json")
    payload.pop("next_rest_url")
    event = payload["events"][0]
    event["cost"] = "10 - 15 €"
    event["website"] = "https://organizer.example.org/tickets"
    event["excerpt"] = "<p>Short <b>summary</b></p>"
    event["venue"] = {"venue": "Sala", "city": "Candelaria", "geo_lat": "28.35", "geo_lng": -16.37}
    event["organizer"] = [
        {"organizer": "Cabildo", "website": "https://cabildo.example.org", "email": "a@b.es"}
    ]
    event["tags"] = [{"name": "Familia"}]
    mock_page("www.candelaria.es", payload)

    first = TribeEventsAdapter(cfg()).fetch(make_ctx()).events[0]

    assert (first.price_min, first.price_max, first.currency) == (10.0, 15.0, "EUR")
    assert first.is_free is False
    assert first.summary == "Short summary"
    assert first.latitude == pytest.approx(28.35) and first.longitude == pytest.approx(-16.37)
    assert first.organizer_name == "Cabildo"
    assert first.organizer_url == "https://cabildo.example.org"
    assert first.contact_email == "a@b.es"
    assert "Familia" in first.tags
    assert first.extra["website"] == "https://organizer.example.org/tickets"


@respx.mock
def test_free_cost_text(make_ctx: MakeCtx) -> None:
    payload = fixture_json("tribe_candelaria.json")
    payload.pop("next_rest_url")
    payload["events"][0]["cost"] = "Gratis"
    mock_page("www.candelaria.es", payload)

    first = TribeEventsAdapter(cfg()).fetch(make_ctx()).events[0]

    assert first.is_free is True and first.price_min == 0.0


@respx.mock
def test_cost_details_values_when_cost_text_is_empty(make_ctx: MakeCtx) -> None:
    payload = fixture_json("tribe_candelaria.json")
    payload.pop("next_rest_url")
    payload["events"][0]["cost_details"] = {
        "currency_symbol": "€",
        "currency_code": "EU",
        "values": ["12", "20"],
    }
    mock_page("www.candelaria.es", payload)

    first = TribeEventsAdapter(cfg()).fetch(make_ctx()).events[0]

    assert (first.price_min, first.price_max, first.currency) == (12.0, 20.0, "EUR")


# --------------------------------------------------------------------------- request


@respx.mock
def test_requests_window_and_page_size(make_ctx: MakeCtx) -> None:
    route = mock_page("www.candelaria.es", {"events": [], "total": 0, "total_pages": 0})

    TribeEventsAdapter(cfg()).fetch(make_ctx())

    query = dict(route.calls.last.request.url.params)
    assert query["start_date"] == "2026-10-19"
    assert query["end_date"] == "2026-10-25"
    assert query["per_page"] == "50"


@respx.mock
def test_empty_calendar(make_ctx: MakeCtx) -> None:
    mock_page("www.lossilos.es", fixture_json("tribe_empty.json"))

    result = TribeEventsAdapter(cfg("www.lossilos.es")).fetch(make_ctx())

    assert result.events == [] and result.records_received == 0 and result.pages == 1


# --------------------------------------------------------------------------- pagination


@respx.mock
def test_follows_next_rest_url_on_same_host(make_ctx: MakeCtx) -> None:
    first = mock_page("www.candelaria.es", fixture_json("tribe_candelaria.json"))
    second = mock_page(
        "www.candelaria.es",
        fixture_json("tribe_candelaria_page2.json"),
        "/wp-json/tribe/events/v1/events/",
    )

    result = TribeEventsAdapter(cfg()).fetch(make_ctx())

    assert result.pages == 2
    assert result.records_received == 6 and len(result.events) == 6
    assert first.call_count == 1 and second.call_count == 1
    assert len({e.source_event_id for e in result.events}) == 6


@respx.mock
def test_foreign_host_next_link_is_ignored(make_ctx: MakeCtx) -> None:
    payload = fixture_json("tribe_candelaria.json")
    payload["next_rest_url"] = "https://evil.example.net/wp-json/tribe/events/v1/events/?page=2"
    mock_page("www.candelaria.es", payload)
    evil = respx.get(host="evil.example.net").mock(return_value=httpx.Response(200, json={}))

    result = TribeEventsAdapter(cfg()).fetch(make_ctx())

    assert evil.call_count == 0
    assert result.pages == 1 and len(result.events) == 3
    assert any("different host" in w for w in result.warnings)


@respx.mock
def test_max_pages_cap_warns(make_ctx: MakeCtx) -> None:
    mock_page("www.candelaria.es", fixture_json("tribe_candelaria.json"))

    result = TribeEventsAdapter(cfg(params={"max_pages": 1})).fetch(make_ctx())

    assert result.pages == 1
    assert any("max_pages" in w for w in result.warnings)


# --------------------------------------------------------------------------- discards / errors


@respx.mock
def test_incomplete_records_are_discarded(make_ctx: MakeCtx) -> None:
    payload = fixture_json("tribe_candelaria.json")
    payload.pop("next_rest_url")
    no_title = copy.deepcopy(payload["events"][0])
    no_title.update(id=1001, title="  ")
    no_start = copy.deepcopy(payload["events"][1])
    no_start.update(id=1002, start_date=None)
    bad_start = copy.deepcopy(payload["events"][1])
    bad_start.update(id=1003, start_date="soon")
    payload["events"] += [no_title, no_start, bad_start]
    mock_page("www.candelaria.es", payload)

    result = TribeEventsAdapter(cfg()).fetch(make_ctx())

    assert result.records_received == 6
    assert len(result.events) == 3
    assert result.discarded == [
        ("missing title", "1001"),
        ("missing start date", "1002"),
        ("missing start date", "1003"),
    ]


@respx.mock
def test_http_error_propagates(make_ctx: MakeCtx) -> None:
    respx.get(host="www.candelaria.es").mock(return_value=httpx.Response(500))

    with pytest.raises(HttpError):
        TribeEventsAdapter(cfg()).fetch(make_ctx())


@respx.mock
def test_non_object_payload_is_invalid(make_ctx: MakeCtx) -> None:
    mock_page("www.candelaria.es", ["not", "an", "object"])

    with pytest.raises(InvalidPayload):
        TribeEventsAdapter(cfg()).fetch(make_ctx())
