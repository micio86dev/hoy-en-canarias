from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

import httpx
import pytest
import respx

from canarias_events.date_range import TZ
from canarias_events.http import HttpError, InvalidPayload
from canarias_events.sources.base import FetchContext, SourceConfig
from canarias_events.sources.rss import RssAdapter
from tests.conftest import fixture_bytes

MakeCtx = Callable[..., FetchContext]
URL = "https://example.org/feed"


def cfg(**overrides: object) -> SourceConfig:
    data: dict[str, object] = {
        "id": "demo_rss",
        "name": "Demo feed",
        "adapter": "rss",
        "url": URL,
        "islands": ["Tenerife"],
    }
    data.update(overrides)
    return SourceConfig.model_validate(data)


def serve(body: bytes) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, content=body))


@respx.mock
def test_rss_item_with_structured_date_is_accepted(make_ctx: MakeCtx) -> None:
    serve(fixture_bytes("sample.rss"))
    config = cfg()

    result = RssAdapter(config).fetch(make_ctx())

    assert result.records_received == 3 and result.pages == 1
    assert [e.title for e in result.events] == [
        "Concierto de la Banda Municipal",
        "Mercadillo del agricultor",
    ]
    concert = result.events[0]
    assert concert.start_datetime == datetime(2026, 10, 22, 20, 0, tzinfo=TZ)
    assert concert.end_datetime == datetime(2026, 10, 22, 22, 0, tzinfo=TZ)
    assert concert.all_day is False
    assert concert.category == "Música"
    assert concert.description == "Concierto en la plaza."
    assert concert.official_event_url == "https://example.org/agenda/concierto-banda"
    assert concert.source_event_id == "evt-001"
    assert concert.source_url == URL and concert.source_type == "rss"
    assert concert.venue_name == "Plaza de la Iglesia, Candelaria"
    assert concert.municipality == "Candelaria" and concert.island == "Tenerife"
    assert concert.raw_data["id"] == "evt-001"


@respx.mock
def test_date_only_value_is_all_day(make_ctx: MakeCtx) -> None:
    serve(fixture_bytes("sample.rss"))

    market = RssAdapter(cfg()).fetch(make_ctx()).events[1]

    assert market.all_day is True and market.start_date == date(2026, 10, 24)
    assert market.start_datetime is None


@respx.mock
def test_item_without_structured_date_is_discarded(make_ctx: MakeCtx) -> None:
    serve(fixture_bytes("sample.rss"))

    result = RssAdapter(cfg()).fetch(make_ctx())

    # The headline mentions a Saturday date, but dates are never parsed out of prose.
    assert result.discarded == [("no structured event date", "news-003")]


@respx.mock
def test_atom_feed(make_ctx: MakeCtx) -> None:
    serve(fixture_bytes("sample.atom"))

    result = RssAdapter(cfg()).fetch(make_ctx())

    assert result.records_received == 2
    assert [e.title for e in result.events] == ["Exposición de fotografía"]
    expo = result.events[0]
    assert expo.start_datetime == datetime(2026, 10, 20, 10, 0, tzinfo=TZ)
    assert expo.official_event_url == "https://example.org/agenda/exposicion-fotografia"
    assert expo.source_event_id == "urn:example:agenda:100"
    assert expo.category == "Exposiciones"
    assert result.discarded == [("no structured event date", "urn:example:agenda:101")]


@respx.mock
def test_configured_date_field(make_ctx: MakeCtx) -> None:
    body = (
        b'<?xml version="1.0"?><rss version="2.0" xmlns:xcal="urn:ietf:params:xml:ns:xcal">'
        b"<channel><title>t</title><item><title>Taller</title><link>https://e.org/t</link>"
        b"<xcal:dtstart>20261022T200000</xcal:dtstart></item></channel></rss>"
    )
    serve(body)

    result = RssAdapter(cfg(params={"date_field": "xcal_dtstart"})).fetch(make_ctx())

    assert result.events[0].start_datetime == datetime(2026, 10, 22, 20, 0, tzinfo=TZ)
    assert result.events[0].source_event_id == "https://e.org/t"


@respx.mock
def test_pubdate_is_never_used_as_event_date(make_ctx: MakeCtx) -> None:
    body = (
        b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title><item>'
        b"<title>Post</title><pubDate>Wed, 14 Oct 2026 09:00:00 +0100</pubDate></item>"
        b"</channel></rss>"
    )
    serve(body)

    result = RssAdapter(cfg()).fetch(make_ctx())

    assert result.events == [] and result.discarded == [("no structured event date", None)]


@respx.mock
def test_item_without_title_is_discarded(make_ctx: MakeCtx) -> None:
    body = (
        b'<?xml version="1.0"?><rss version="2.0" '
        b'xmlns:ev="http://purl.org/rss/1.0/modules/event/"><channel><title>t</title><item>'
        b"<guid>g1</guid><ev:startdate>2026-10-22</ev:startdate></item></channel></rss>"
    )
    serve(body)

    assert RssAdapter(cfg()).fetch(make_ctx()).discarded == [("missing title", "g1")]


@respx.mock
def test_http_error_propagates(make_ctx: MakeCtx) -> None:
    respx.get(URL).mock(return_value=httpx.Response(503))

    with pytest.raises(HttpError):
        RssAdapter(cfg()).fetch(make_ctx())


@respx.mock
def test_non_feed_body_is_invalid(make_ctx: MakeCtx) -> None:
    serve(b"this is definitely not a feed")

    with pytest.raises(InvalidPayload):
        RssAdapter(cfg()).fetch(make_ctx())
