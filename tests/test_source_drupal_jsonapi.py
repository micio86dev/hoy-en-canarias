from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx
import pytest
import respx

from canarias_events.http import HttpError, InvalidPayload
from canarias_events.sources.base import FetchContext, SourceConfig
from canarias_events.sources.drupal_jsonapi import DrupalJsonApiAdapter
from tests.conftest import fixture_json

MakeCtx = Callable[..., FetchContext]
HOST = "www.holaislascanarias.com"
PATH = "/jsonapi/node/event"
LICENSE = "unknown - ToS not published"


def cfg(**overrides: Any) -> SourceConfig:
    data: dict[str, Any] = {
        "id": "holaislascanarias",
        "name": "Hola Islas Canarias",
        "adapter": "drupal_jsonapi",
        "url": f"https://{HOST}{PATH}",
        "islands": [
            "Tenerife",
            "Gran Canaria",
            "Lanzarote",
            "Fuerteventura",
            "La Palma",
            "La Gomera",
            "El Hierro",
            "La Graciosa",
        ],  # fmt: skip
        "license": LICENSE,
    }
    data.update(overrides)
    return SourceConfig.model_validate(data)


def paged(pages: list[Any]) -> Callable[[httpx.Request], httpx.Response]:
    """Serve pages in order, selected by the JSON:API ``page[offset]`` query value."""

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("page[offset]", "0"))
        index = offset // 4
        return httpx.Response(200, json=pages[index])

    return handler


def single(payload: Any) -> None:
    respx.get(host=HOST, path=PATH).mock(return_value=httpx.Response(200, json=payload))


# --------------------------------------------------------------------------- mapping


@respx.mock
def test_maps_real_events(make_ctx: MakeCtx) -> None:
    payload = fixture_json("holaislascanarias_events_page1.json")
    payload["links"].pop("next")
    single(payload)
    config = cfg()

    result = DrupalJsonApiAdapter(config).fetch(make_ctx())

    assert result.records_received == 4 and len(result.events) == 4 and result.pages == 1
    danza = result.events[0]
    assert danza.title == "Festival de Danza Traslación"
    assert danza.all_day is True
    assert danza.start_date == date(2026, 10, 20) and danza.end_date == date(2026, 10, 31)
    assert danza.start_datetime is None
    assert danza.island == "Lanzarote" and danza.municipality == "Arrecife"
    assert danza.category == "Festival"
    assert danza.is_free is True and danza.price_min == 0.0 and danza.price_max == 0.0
    assert danza.latitude == pytest.approx(28.958581)
    assert danza.longitude == pytest.approx(-13.549445)
    assert danza.source_name == "Hola Islas Canarias"
    assert danza.source_url == config.url
    assert danza.official_event_url == (
        "https://www.holaislascanarias.com/eventos/lanzarote/festival-de-danza-traslacion"
    )
    assert danza.source_type == "drupal_jsonapi"
    assert danza.source_license == LICENSE
    assert danza.source_event_id == payload["data"][0]["id"]
    assert danza.extra["event_website"] == "https://www.festivaltraslacion.com/"
    assert danza.raw_data["id"] == payload["data"][0]["id"]
    assert danza.description and "\r" not in danza.description


@respx.mock
def test_free_flag_unknown_and_tickets(make_ctx: MakeCtx) -> None:
    single(fixture_json("holaislascanarias_events_page2.json"))

    events = DrupalJsonApiAdapter(cfg()).fetch(make_ctx()).events

    by_title = {e.title: e for e in events}
    oreja = by_title["La Oreja de Van Gogh en concierto"]
    assert oreja.is_free is None  # "0" only means "not flagged free"
    assert oreja.price_min is None
    assert oreja.ticket_url and oreja.ticket_url.startswith("https://www.entradas.com/")
    assert oreja.category == "Música"
    assert by_title["Fiesta de San Simón"].is_free is True


@respx.mock
def test_island_and_municipality_from_included_terms(make_ctx: MakeCtx) -> None:
    single(fixture_json("holaislascanarias_events_page2.json"))

    events = {e.title: e for e in DrupalJsonApiAdapter(cfg()).fetch(make_ctx()).events}

    assert events["Fiesta de San Simón"].island == "El Hierro"
    assert events["Fiesta de San Simón"].municipality == "Frontera"
    # No municipality term: the free-text city is used only because it is a real municipality.
    manga = events["CANARIAS MANGA FESTIVAL"]
    assert manga.island == "Fuerteventura" and manga.municipality == "Puerto del Rosario"
    assert events["Tenerife Peñón Fest"].municipality == "Santa Cruz de Tenerife"


@respx.mock
def test_multiple_islands_leave_island_unknown(make_ctx: MakeCtx) -> None:
    payload = fixture_json("holaislascanarias_events_page1.json")
    payload["links"].pop("next")
    item = payload["data"][0]
    item["relationships"]["field_t_islands"]["data"] = [
        {"type": "taxonomy_term--islands", "id": "x1"},
        {"type": "taxonomy_term--islands", "id": "x2"},
    ]
    payload["included"] += [
        {"type": "taxonomy_term--islands", "id": "x1", "attributes": {"name": "Lanzarote"}},
        {"type": "taxonomy_term--islands", "id": "x2", "attributes": {"name": "Tenerife"}},
    ]
    item["relationships"]["field_t_municipality"]["data"] = []
    item["attributes"]["field_event_city"] = "Varias localidades"
    single(payload)

    danza = DrupalJsonApiAdapter(cfg()).fetch(make_ctx()).events[0]

    assert danza.island is None and danza.municipality is None
    assert danza.extra["islands"] == ["Lanzarote", "Tenerife"]


@respx.mock
def test_long_running_and_typo_dates_are_kept_as_is(make_ctx: MakeCtx) -> None:
    payload = fixture_json("holaislascanarias_events_longrunning.json")
    payload["links"].pop("next")
    single(payload)

    events = DrupalJsonApiAdapter(cfg()).fetch(make_ctx()).events

    assert events[0].start_date == date(26, 12, 6)  # source typo, never "fixed"
    assert events[1].start_date == date(2016, 11, 28) and events[1].end_date == date(2026, 12, 8)


# --------------------------------------------------------------------------- request


@respx.mock
def test_request_filters_window_and_includes_terms(make_ctx: MakeCtx) -> None:
    payload = fixture_json("holaislascanarias_events_page2.json")
    route = respx.get(host=HOST, path=PATH).mock(return_value=httpx.Response(200, json=payload))

    DrupalJsonApiAdapter(cfg()).fetch(make_ctx())

    query = route.calls.last.request.url.params
    assert query["filter[s][condition][path]"] == "field_e_start_date"
    assert query["filter[s][condition][operator]"] == "<"
    assert query["filter[s][condition][value]"] == "2026-10-26"
    assert query["filter[e][condition][path]"] == "field_e_calculated_end_date"
    assert query["filter[e][condition][operator]"] == ">="
    assert query["filter[e][condition][value]"] == "2026-10-19"
    assert query["sort"] == "field_e_start_date"
    assert query["page[limit]"] == "50"
    assert "field_t_islands" in query["include"] and "field_t_municipality" in query["include"]


# --------------------------------------------------------------------------- pagination


@respx.mock
def test_follows_links_next_until_exhausted(make_ctx: MakeCtx) -> None:
    pages = [
        fixture_json("holaislascanarias_events_page1.json"),
        fixture_json("holaislascanarias_events_page2.json"),
    ]
    route = respx.get(host=HOST, path=PATH).mock(side_effect=paged(pages))

    result = DrupalJsonApiAdapter(cfg()).fetch(make_ctx())

    assert route.call_count == 2
    assert result.pages == 2 and result.records_received == 8 and len(result.events) == 8
    assert result.warnings == []


@respx.mock
def test_foreign_host_next_link_is_ignored(make_ctx: MakeCtx) -> None:
    payload = fixture_json("holaislascanarias_events_page1.json")
    payload["links"]["next"]["href"] = "https://evil.example.net/jsonapi/node/event?page=2"
    single(payload)
    evil = respx.get(host="evil.example.net").mock(return_value=httpx.Response(200, json={}))

    result = DrupalJsonApiAdapter(cfg()).fetch(make_ctx())

    assert evil.call_count == 0
    assert result.pages == 1 and len(result.events) == 4
    assert any("different host" in w for w in result.warnings)


@respx.mock
def test_max_pages_cap_warns(make_ctx: MakeCtx) -> None:
    single(fixture_json("holaislascanarias_events_page1.json"))

    result = DrupalJsonApiAdapter(cfg(params={"max_pages": 1})).fetch(make_ctx())

    assert result.pages == 1
    assert any("max_pages" in w for w in result.warnings)


# --------------------------------------------------------------------------- discards / errors


@respx.mock
def test_incomplete_records_are_discarded(make_ctx: MakeCtx) -> None:
    payload = fixture_json("holaislascanarias_events_page1.json")
    payload["links"].pop("next")
    no_title = copy.deepcopy(payload["data"][0])
    no_title["id"] = "no-title"
    no_title["attributes"]["title"] = ""
    no_start = copy.deepcopy(payload["data"][1])
    no_start["id"] = "no-start"
    no_start["attributes"]["field_e_start_date"] = None
    bad_start = copy.deepcopy(payload["data"][1])
    bad_start["id"] = "bad-start"
    bad_start["attributes"]["field_e_start_date"] = "2026-13-45"
    payload["data"] += [no_title, no_start, bad_start]
    single(payload)

    result = DrupalJsonApiAdapter(cfg()).fetch(make_ctx())

    assert result.records_received == 7 and len(result.events) == 4
    assert result.discarded == [
        ("missing title", "no-title"),
        ("missing start date", "no-start"),
        ("missing start date", "bad-start"),
    ]


@respx.mock
def test_http_error_propagates(make_ctx: MakeCtx) -> None:
    respx.get(host=HOST).mock(return_value=httpx.Response(503))

    with pytest.raises(HttpError):
        DrupalJsonApiAdapter(cfg()).fetch(make_ctx())


@respx.mock
def test_payload_without_data_list_is_invalid(make_ctx: MakeCtx) -> None:
    single({"errors": [{"title": "Forbidden"}]})

    with pytest.raises(InvalidPayload):
        DrupalJsonApiAdapter(cfg()).fetch(make_ctx())
