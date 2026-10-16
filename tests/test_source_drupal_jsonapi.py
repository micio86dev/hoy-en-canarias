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


# --------------------------------------------------------------------------- media and tags

ORIGIN = f"https://{HOST}"


def media_events(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or fixture_json("holaislascanarias_events_media.json")
    data.get("links", {}).pop("next", None)
    return data


def fetch_media(make_ctx: MakeCtx, payload: dict[str, Any]) -> dict[str, Any]:
    single(payload)
    events = DrupalJsonApiAdapter(cfg()).fetch(make_ctx()).events
    return {e.title: e for e in events}


@respx.mock
def test_header_image_wins_and_gallery_follows(make_ctx: MakeCtx) -> None:
    events = fetch_media(make_ctx, media_events())

    batalla = events["Batalla del Cuchillete y Tamasite"]
    header = f"{ORIGIN}/sites/default/files/events/batalla%20tamasite.jpg"
    gallery = f"{ORIGIN}/sites/default/files/events/batalla_tamasite_1.jpg"
    assert batalla.image_url == header
    images = batalla.extra["images"]
    assert [(i["url"], i["role"]) for i in images] == [(header, "header"), (gallery, "gallery")]
    assert set(images[0]) == {"url", "alt", "width", "height", "role"}


@respx.mock
def test_gallery_image_used_when_no_header(make_ctx: MakeCtx) -> None:
    events = fetch_media(make_ctx, media_events())

    trail = events["Trail Pino Santo"]
    url = f"{ORIGIN}/sites/default/files/events/trail_pino_santo2025_1010.png"
    assert trail.image_url == url
    assert [i["role"] for i in trail.extra["images"]] == ["gallery"]
    assert trail.extra["images"][0]["url"] == url


@respx.mock
def test_image_dimensions_and_alt_come_from_relationship_meta(make_ctx: MakeCtx) -> None:
    payload = media_events()
    media = next(i for i in payload["included"] if i["attributes"].get("name") == "cana_dulce")
    ref = media["relationships"]["field_media_image"]["data"]
    ref["meta"] = {"alt": "Cana dulce", "title": "t", "width": 800, "height": 600}

    events = fetch_media(make_ctx, payload)

    image = events["Fiestas de la Caña Dulce y La Concepción"].extra["images"][0]
    assert (image["alt"], image["width"], image["height"]) == ("Cana dulce", 800, 600)


@respx.mock
def test_missing_file_is_skipped_never_guessed(make_ctx: MakeCtx) -> None:
    payload = media_events()
    payload["included"] = [
        i
        for i in payload["included"]
        if not (i["type"] == "file--file" and "trail_pino" in i["attributes"]["uri"]["url"])
    ]

    trail = fetch_media(make_ctx, payload)["Trail Pino Santo"]

    assert trail.image_url is None
    assert "images" not in trail.extra


@respx.mock
def test_file_without_url_and_non_http_urls_are_skipped(make_ctx: MakeCtx) -> None:
    payload = media_events()
    for item in payload["included"]:
        if item["type"] != "file--file":
            continue
        uri = item["attributes"]["uri"]
        if "cana_dulce" in uri["url"]:
            item["attributes"]["uri"] = {"value": uri["value"]}
        if "origen_dinner" in uri["url"]:
            uri["url"] = "javascript:alert(1)"

    events = fetch_media(make_ctx, payload)

    assert "images" not in events["Fiestas de la Caña Dulce y La Concepción"].extra
    assert "images" not in events["Origen Dinner Show"].extra
    assert events["Origen Dinner Show"].image_url is None


@respx.mock
def test_media_relationship_may_be_a_list_and_images_are_deduplicated(
    make_ctx: MakeCtx,
) -> None:
    payload = media_events()
    item = payload["data"][1]  # Trail Pino Santo
    ref = item["relationships"]["field_h_image_gallery"]["data"]
    item["relationships"]["field_h_image_gallery"]["data"] = [ref, copy.deepcopy(ref)]

    trail = fetch_media(make_ctx, payload)["Trail Pino Santo"]

    assert len(trail.extra["images"]) == 1


@respx.mock
def test_tags_come_from_field_t_tags(make_ctx: MakeCtx) -> None:
    payload = media_events()
    payload["data"][0]["relationships"]["field_t_tags"]["data"] = [
        {"type": "taxonomy_term--tags", "id": "t1"},
        {"type": "taxonomy_term--tags", "id": "t2"},
    ]
    payload["included"] += [
        {"type": "taxonomy_term--tags", "id": "t1", "attributes": {"name": "Senderismo"}},
        {"type": "taxonomy_term--tags", "id": "t2", "attributes": {"name": "Tradición"}},
    ]

    batalla = fetch_media(make_ctx, payload)["Batalla del Cuchillete y Tamasite"]

    assert batalla.tags == ["Senderismo", "Tradición"]


@respx.mock
def test_events_without_media_keep_working(make_ctx: MakeCtx) -> None:
    payload = fixture_json("holaislascanarias_events_page2.json")
    payload["links"].pop("next", None)

    events = fetch_media(make_ctx, payload)

    assert all("images" not in e.extra for e in events.values())


@respx.mock
def test_request_includes_media_and_tags(make_ctx: MakeCtx) -> None:
    payload = media_events()
    route = respx.get(host=HOST, path=PATH).mock(return_value=httpx.Response(200, json=payload))

    DrupalJsonApiAdapter(cfg()).fetch(make_ctx())

    query = route.calls.last.request.url.params
    includes = query["include"].split(",")
    for path in (
        "field_t_tags",
        "field_h_images",
        "field_h_images.field_media_image",
        "field_h_image_gallery",
        "field_h_image_gallery.field_media_image",
    ):
        assert path in includes
    for field in ("field_t_tags", "field_h_images", "field_h_image_gallery"):
        assert field in query["fields[node--event]"].split(",")
    assert query["fields[taxonomy_term--tags]"] == "name"
    assert query["fields[media--header_images]"] == "name,field_media_image"
    assert query["fields[media--list_image]"] == "name,field_media_image"
    assert query["fields[file--file]"] == "uri"
