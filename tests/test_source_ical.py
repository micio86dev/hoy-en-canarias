from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

import httpx
import pytest
import respx

from canarias_events.date_range import TZ, DateWindow, explicit_window
from canarias_events.http import HttpError, InvalidPayload
from canarias_events.sources.base import FetchContext, SourceConfig
from canarias_events.sources.ical import IcalAdapter
from tests.conftest import fixture_bytes

MakeCtx = Callable[..., FetchContext]
WIDE: DateWindow = explicit_window(date(2026, 10, 15), date(2026, 10, 31))


def cfg(host: str = "deportestenerife.es", **overrides: object) -> SourceConfig:
    data: dict[str, object] = {
        "id": "demo_ical",
        "name": "Demo iCal",
        "adapter": "ical",
        "url": f"https://{host}/?ical=1",
        "islands": ["Tenerife"],
    }
    data.update(overrides)
    return SourceConfig.model_validate(data)


def serve(host: str, body: bytes) -> respx.Route:
    return respx.get(host=host).mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/calendar"})
    )


def ics(*events: str) -> bytes:
    body = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
    body += "".join(f"BEGIN:VEVENT\r\n{e.strip()}\r\nEND:VEVENT\r\n" for e in events)
    return (body + "END:VCALENDAR\r\n").encode()


# --------------------------------------------------------------------------- real fixtures


@respx.mock
def test_maps_deportes_tenerife_ics_with_forced_timezone(make_ctx: MakeCtx) -> None:
    serve("deportestenerife.es", fixture_bytes("deportestenerife.ics"))
    config = cfg(params={"force_timezone": "Atlantic/Canary"})

    result = IcalAdapter(config).fetch(make_ctx(window=WIDE))

    assert result.records_received == 3 and len(result.events) == 3 and result.pages == 1
    judo = result.events[0]
    assert judo.title == "Primera Jornada de Judo de los XL Juegos Cabildo"
    # The feed labels local wall-clock times as UTC; force_timezone reinterprets them.
    assert judo.start_datetime == datetime(2026, 10, 17, 8, 30, tzinfo=TZ)
    assert judo.end_datetime == datetime(2026, 10, 17, 14, 0, tzinfo=TZ)
    assert judo.all_day is False
    assert judo.source_event_id == "10002914-1792225800-1792245600@deportestenerife.es"
    assert judo.official_event_url == (
        "https://deportestenerife.es/evento/primera-jornada-de-judo-de-los-xl-juegos-cabildo/"
    )
    assert judo.source_url == config.url
    assert judo.source_type == "ical"
    assert judo.venue_name and judo.venue_name.startswith("Pabellón Municipal Rosendo Alonso Tapia")
    assert judo.municipality == "Candelaria" and judo.island == "Tenerife"
    assert judo.category == "Juegos Cabildo"
    assert judo.image_url == "https://deportestenerife.es/wp-content/uploads/2026/04/Judo-1.png"
    assert judo.description and judo.description.startswith("Primera jornada de Judo")
    assert "UID" in judo.raw_data and judo.raw_data["UID"] == judo.source_event_id


@respx.mock
def test_utc_tzid_is_honoured_without_force_timezone(make_ctx: MakeCtx) -> None:
    serve("deportestenerife.es", fixture_bytes("deportestenerife.ics"))

    judo = IcalAdapter(cfg()).fetch(make_ctx(window=WIDE)).events[0]

    assert judo.start_datetime == datetime(2026, 10, 17, 9, 30, tzinfo=TZ)  # 08:30 UTC


@respx.mock
def test_antigua_all_day_and_timed_events(make_ctx: MakeCtx) -> None:
    serve("ayto-antigua.es", fixture_bytes("antigua.ics"))
    config = cfg("ayto-antigua.es", islands=["Fuerteventura"], municipality="Antigua")

    result = IcalAdapter(config).fetch(make_ctx())  # default next-week window

    titles = {e.title: e for e in result.events}
    assert result.records_received == 7
    shop = titles["Concurso de Escaparates: Halloween y Finaos"]
    assert shop.all_day is True and shop.start_datetime is None
    assert shop.start_date == date(2026, 10, 1)
    assert shop.end_date == date(2026, 10, 20)  # DTEND;VALUE=DATE is exclusive
    night = titles["Noche en Blanco Antigua 2026"]
    assert night.start_datetime == datetime(2026, 10, 24, 17, 0, tzinfo=TZ)
    assert night.end_datetime == datetime(2026, 10, 25, 1, 0, tzinfo=TZ)
    assert night.municipality == "Antigua" and night.island == "Fuerteventura"
    assert night.category is None


@respx.mock
def test_events_outside_window_are_not_returned(make_ctx: MakeCtx) -> None:
    serve("deportestenerife.es", fixture_bytes("deportestenerife.ics"))

    result = IcalAdapter(cfg()).fetch(make_ctx())  # 19-25 Oct; the events are on the 17th

    assert result.events == [] and result.records_received == 3


# --------------------------------------------------------------------------- recurrence


@respx.mock
def test_rrule_is_expanded_inside_the_window(make_ctx: MakeCtx) -> None:
    body = ics(
        "UID:weekly@example.org\r\nSUMMARY:Clase semanal\r\n"
        "DTSTART;TZID=Atlantic/Canary:20261012T180000\r\n"
        "DTEND;TZID=Atlantic/Canary:20261012T190000\r\n"
        "RRULE:FREQ=WEEKLY;COUNT=10"
    )
    serve("deportestenerife.es", body)

    result = IcalAdapter(cfg()).fetch(make_ctx())  # window 19-25 Oct

    assert result.records_received == 1  # one VEVENT component
    assert len(result.events) == 1
    event = result.events[0]
    assert event.start_datetime == datetime(2026, 10, 19, 18, 0, tzinfo=TZ)
    assert event.end_datetime == datetime(2026, 10, 19, 19, 0, tzinfo=TZ)
    assert event.source_event_id == "weekly@example.org#2026-10-19T18:00:00+01:00"


@respx.mock
def test_daily_rrule_gives_one_event_per_day(make_ctx: MakeCtx) -> None:
    body = ics(
        "UID:daily@example.org\r\nSUMMARY:Mercadillo\r\n"
        "DTSTART;VALUE=DATE:20261019\r\nRRULE:FREQ=DAILY;COUNT=3"
    )
    serve("deportestenerife.es", body)

    events = IcalAdapter(cfg()).fetch(make_ctx()).events

    assert [e.start_date for e in events] == [
        date(2026, 10, 19),
        date(2026, 10, 20),
        date(2026, 10, 21),
    ]
    assert all(e.all_day for e in events)
    assert len({e.source_event_id for e in events}) == 3
    assert events[0].end_date is None  # single-day all-day event without DTEND


@respx.mock
def test_floating_times_use_canary(make_ctx: MakeCtx) -> None:
    serve("deportestenerife.es", ics("UID:f@x\r\nSUMMARY:Floating\r\nDTSTART:20261020T100000"))

    event = IcalAdapter(cfg()).fetch(make_ctx()).events[0]

    assert event.start_datetime == datetime(2026, 10, 20, 10, 0, tzinfo=TZ)


# --------------------------------------------------------------------------- other properties


@respx.mock
def test_geo_categories_url_and_location_parsing(make_ctx: MakeCtx) -> None:
    body = ics(
        "UID:g@x\r\nSUMMARY:Concierto\r\nDTSTART;TZID=Atlantic/Canary:20261021T200000\r\n"
        "GEO:28.4636;-16.2518\r\nCATEGORIES:Música,Familia\r\n"
        "URL:https://example.org/e/1\r\nLOCATION:Plaza Mayor\\, Arona\r\n"
        "STATUS:CONFIRMED\r\nDESCRIPTION:<p>Entrada <b>libre</b></p>"
    )
    serve("deportestenerife.es", body)

    event = IcalAdapter(cfg(islands=[])).fetch(make_ctx()).events[0]

    assert event.latitude == pytest.approx(28.4636) and event.longitude == pytest.approx(-16.2518)
    assert event.category == "Música" and event.tags == ["Familia"]
    assert event.official_event_url == "https://example.org/e/1"
    assert event.venue_name == "Plaza Mayor, Arona"
    assert event.municipality == "Arona" and event.island == "Tenerife"
    assert event.description == "Entrada libre"


@respx.mock
def test_cancelled_and_untitled_events_are_discarded(make_ctx: MakeCtx) -> None:
    body = ics(
        "UID:c@x\r\nSUMMARY:Cancelado\r\nDTSTART;TZID=Atlantic/Canary:20261021T200000\r\nSTATUS:CANCELLED",
        "UID:n@x\r\nDTSTART;TZID=Atlantic/Canary:20261021T200000",
        "UID:ok@x\r\nSUMMARY:Valido\r\nDTSTART;TZID=Atlantic/Canary:20261021T200000",
        "UID:nostart@x\r\nSUMMARY:Sin fecha",
    )
    serve("deportestenerife.es", body)

    result = IcalAdapter(cfg()).fetch(make_ctx())

    assert [e.title for e in result.events] == ["Valido"]
    assert result.records_received == 4
    assert sorted(result.discarded) == [
        ("cancelled", "c@x"),
        ("missing start date", "nostart@x"),
        ("missing title", "n@x"),
    ]


# --------------------------------------------------------------------------- errors


@respx.mock
def test_http_error_propagates(make_ctx: MakeCtx) -> None:
    respx.get(host="deportestenerife.es").mock(return_value=httpx.Response(500))

    with pytest.raises(HttpError):
        IcalAdapter(cfg()).fetch(make_ctx())


@respx.mock
def test_garbage_body_is_invalid_payload(make_ctx: MakeCtx) -> None:
    serve("deportestenerife.es", b"<html>not a calendar</html>")

    with pytest.raises(InvalidPayload):
        IcalAdapter(cfg()).fetch(make_ctx())
