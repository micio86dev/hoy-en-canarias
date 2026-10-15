from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import httpx
import pytest
import respx

from canarias_events.date_range import TZ
from canarias_events.http import HttpError
from canarias_events.sources.base import FetchContext, SourceConfig
from canarias_events.sources.ticketmaster import (
    DEFAULT_ISLAND_POINTS,
    TicketmasterAdapter,
    geohash_encode,
)
from tests.conftest import fixture_json

MakeCtx = Callable[..., FetchContext]
HOST = "app.ticketmaster.com"
PATH = "/discovery/v2/events.json"
KEY = "SECRETKEY123"
ENV = {"TICKETMASTER_API_KEY": KEY}


def cfg(**overrides: Any) -> SourceConfig:
    data: dict[str, Any] = {
        "id": "ticketmaster",
        "name": "Ticketmaster",
        "adapter": "ticketmaster",
        "url": f"https://{HOST}{PATH}",
        "islands": ["Tenerife"],
        "auth_env": "TICKETMASTER_API_KEY",
        "license": "Ticketmaster API terms",
    }
    data.update(overrides)
    return SourceConfig.model_validate(data)


def serve(payload: Any) -> respx.Route:
    return respx.get(host=HOST, path=PATH).mock(return_value=httpx.Response(200, json=payload))


# --------------------------------------------------------------------------- geohash


@pytest.mark.parametrize(
    ("lat", "lon", "expected"),
    [
        (42.605, -5.603, "ezs42"),  # canonical example from the geohash specification
        (28.4636, -16.2518, "eth3y"),  # Santa Cruz de Tenerife, cross-checked with pygeohash
        (28.29, -16.55, "eth0z"),
        (29.23, -13.5, "etnqk"),
        (0.0, 0.0, "s0000"),
        (-90.0, -180.0, "00000"),
        (90.0, 180.0, "zzzzz"),
    ],
)
def test_geohash_known_values(lat: float, lon: float, expected: str) -> None:
    assert geohash_encode(lat, lon, 5) == expected


def test_geohash_precision_is_a_prefix_relation() -> None:
    assert geohash_encode(28.4636, -16.2518, 9).startswith("eth3y")
    assert len(geohash_encode(28.4636, -16.2518, 3)) == 3


def test_default_island_points_cover_all_islands() -> None:
    assert set(DEFAULT_ISLAND_POINTS) == {
        "Tenerife", "Gran Canaria", "Lanzarote", "Fuerteventura",
        "La Palma", "La Gomera", "El Hierro", "La Graciosa",
    }  # fmt: skip


# --------------------------------------------------------------------------- missing key


@respx.mock
def test_missing_key_skips_without_any_request(make_ctx: MakeCtx) -> None:
    route = serve({})

    result = TicketmasterAdapter(cfg()).fetch(make_ctx(env={}))

    assert route.call_count == 0
    assert result.events == [] and result.pages == 0 and result.records_received == 0
    assert result.warnings == ["skipped: missing API key (set TICKETMASTER_API_KEY)"]


@respx.mock
def test_blank_key_counts_as_missing(make_ctx: MakeCtx) -> None:
    route = serve({})

    result = TicketmasterAdapter(cfg()).fetch(make_ctx(env={"TICKETMASTER_API_KEY": "  "}))

    assert route.call_count == 0 and result.warnings


# --------------------------------------------------------------------------- request / mapping


@respx.mock
def test_request_parameters(make_ctx: MakeCtx) -> None:
    route = serve(fixture_json("ticketmaster_page.json"))

    TicketmasterAdapter(cfg()).fetch(make_ctx(env=ENV))

    assert route.call_count == 1
    query = route.calls.last.request.url.params
    lat, lon, radius = DEFAULT_ISLAND_POINTS["Tenerife"]
    assert query["apikey"] == KEY
    assert query["countryCode"] == "ES"
    assert query["geoPoint"] == geohash_encode(lat, lon, 5)
    assert query["radius"] == str(radius) and query["unit"] == "km"
    # Monday 19 Oct 00:00 Canary is still summer time (UTC+1); 26 Oct 00:00 is winter (UTC+0).
    assert query["startDateTime"] == "2026-10-18T23:00:00Z"
    assert query["endDateTime"] == "2026-10-26T00:00:00Z"
    assert query["size"] == "100" and query["page"] == "0"


@respx.mock
def test_maps_documented_schema(make_ctx: MakeCtx) -> None:
    serve(fixture_json("ticketmaster_page.json"))
    config = cfg()

    result = TicketmasterAdapter(config).fetch(make_ctx(env=ENV))

    assert result.pages == 1 and result.records_received == 3
    assert len(result.events) == 2
    assert result.discarded == [("missing title", "G5vYZ9synth03")]
    concert = result.events[0]
    assert concert.title == "Concierto Sinfonico de Otono"
    assert concert.start_datetime == datetime(2026, 10, 23, 20, 0, tzinfo=TZ)  # 19:00Z
    assert concert.end_datetime == datetime(2026, 10, 23, 22, 30, tzinfo=TZ)
    assert concert.all_day is False
    assert concert.venue_name == "Auditorio de Tenerife"
    assert concert.address == "Av. de la Constitucion, 1"
    assert concert.postal_code == "38005"
    assert concert.municipality == "Santa Cruz de Tenerife" and concert.island == "Tenerife"
    assert concert.latitude == pytest.approx(28.4636) and concert.longitude == pytest.approx(
        -16.2518
    )
    assert (concert.price_min, concert.price_max, concert.currency) == (15.0, 45.5, "EUR")
    assert concert.is_free is False
    assert concert.category == "Music" and concert.tags == ["Classical"]
    assert concert.image_url == "https://s1.ticketm.net/img/large.jpg"
    assert (
        concert.ticket_url
        == concert.official_event_url
        == "https://www.ticketmaster.es/event/synth01"
    )
    assert concert.source_event_id == "G5vYZ9synth01"
    assert concert.source_url == config.url and concert.source_type == "ticketmaster"
    assert concert.source_license == "Ticketmaster API terms"
    assert concert.raw_data["id"] == "G5vYZ9synth01"


@respx.mock
def test_date_only_event_and_island_from_city(make_ctx: MakeCtx) -> None:
    serve(fixture_json("ticketmaster_page.json"))

    final = TicketmasterAdapter(cfg(islands=[])).fetch(make_ctx(env=ENV)).events[1]

    assert final.all_day is True and final.start_date == date(2026, 10, 25)
    assert final.start_datetime is None
    assert final.municipality == "Arrecife" and final.island == "Lanzarote"
    assert final.price_min is None and final.is_free is None
    assert final.latitude is None


@respx.mock
def test_one_request_per_configured_island_with_dedup(make_ctx: MakeCtx) -> None:
    route = serve(fixture_json("ticketmaster_page.json"))
    config = cfg(islands=["Tenerife", "Gran Canaria", "Lanzarote"])

    result = TicketmasterAdapter(config).fetch(make_ctx(env=ENV))

    points = {call.request.url.params["geoPoint"] for call in route.calls}
    assert route.call_count == 3 and len(points) == 3
    assert len(result.events) == 2  # same ids returned by each island query
    reasons = [reason for reason, _ in result.discarded]
    assert reasons.count("duplicate event id") == 4
    assert result.records_received == len(result.events) + len(result.discarded)


@respx.mock
def test_no_configured_island_queries_all_eight(make_ctx: MakeCtx) -> None:
    route = serve({"page": {"size": 100, "totalElements": 0, "totalPages": 0, "number": 0}})

    TicketmasterAdapter(cfg(islands=[])).fetch(make_ctx(env=ENV))

    assert route.call_count == 8


@respx.mock
def test_island_point_override(make_ctx: MakeCtx) -> None:
    route = serve({"page": {"totalPages": 0}})
    config = cfg(
        params={"island_points": {"Tenerife": {"lat": 42.605, "lon": -5.603, "radius": 7}}}
    )

    TicketmasterAdapter(config).fetch(make_ctx(env=ENV))

    query = route.calls.last.request.url.params
    assert query["geoPoint"] == "ezs42" and query["radius"] == "7"


# --------------------------------------------------------------------------- pagination


def _page(number: int, total_pages: int) -> dict[str, Any]:
    template = fixture_json("ticketmaster_page.json")["_embedded"]["events"][0]
    events = []
    for index in range(100):
        event = copy.deepcopy(template)
        event["id"] = f"p{number}-e{index}"
        events.append(event)
    return {
        "_embedded": {"events": events},
        "page": {
            "size": 100,
            "totalElements": total_pages * 100,
            "totalPages": total_pages,
            "number": number,
        },
    }


@respx.mock
def test_stops_at_total_pages(make_ctx: MakeCtx) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_page(int(request.url.params["page"]), 3))

    route = respx.get(host=HOST, path=PATH).mock(side_effect=handler)

    result = TicketmasterAdapter(cfg()).fetch(make_ctx(env=ENV))

    assert route.call_count == 3 and result.pages == 3 and len(result.events) == 300
    assert result.warnings == []


@respx.mock
def test_deep_paging_is_capped_at_1000_items(make_ctx: MakeCtx) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_page(int(request.url.params["page"]), 30))

    route = respx.get(host=HOST, path=PATH).mock(side_effect=handler)

    result = TicketmasterAdapter(cfg()).fetch(make_ctx(env=ENV))

    assert route.call_count == 10 and result.pages == 10 and len(result.events) == 1000
    assert any("1000" in w for w in result.warnings)


# --------------------------------------------------------------------------- secrets


@respx.mock
def test_key_never_appears_in_logs_warnings_or_events(
    make_ctx: MakeCtx, caplog: pytest.LogCaptureFixture
) -> None:
    serve(fixture_json("ticketmaster_page.json"))
    caplog.set_level(logging.DEBUG)

    result = TicketmasterAdapter(cfg()).fetch(make_ctx(env=ENV))

    assert KEY not in caplog.text
    assert all(KEY not in w for w in result.warnings)
    assert all(KEY not in (e.source_url or "") for e in result.events)
    assert all(KEY not in repr(e.raw_data) for e in result.events)


@respx.mock
def test_http_error_does_not_leak_key(make_ctx: MakeCtx) -> None:
    respx.get(host=HOST, path=PATH).mock(return_value=httpx.Response(401))

    with pytest.raises(HttpError) as excinfo:
        TicketmasterAdapter(cfg()).fetch(make_ctx(env=ENV))

    assert KEY not in str(excinfo.value)
