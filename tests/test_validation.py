"""validate-source: reachability, robots, events, quality, coverage and a suggested mode."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from canarias_events.database import Database, SourceRun
from canarias_events.http import InvalidPayload, SafeHttpClient
from canarias_events.sources.base import FetchContext, SourceConfig
from canarias_events.validation import ValidationReport, validate_source
from tests.conftest import NOW

MakeCtx = Callable[..., FetchContext]
HOST = "agenda.example.org"
URL = f"https://{HOST}/programacion/"


def client(make_ctx: MakeCtx) -> SafeHttpClient:
    return make_ctx().client


def robots(body: str | None = None, status: int = 404) -> respx.Route:
    response = httpx.Response(200, text=body) if body is not None else httpx.Response(status)
    return respx.get(f"https://{HOST}/robots.txt").mock(return_value=response)


def html(body: str, status: int = 200, ctype: str = "text/html; charset=utf-8") -> respx.Route:
    return respx.get(URL).mock(
        return_value=httpx.Response(status, text=body, headers={"content-type": ctype})
    )


def event_ld(name: str, start: datetime, *, place: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {"@type": "Event", "name": name, "startDate": start.isoformat()}
    if place:
        data["location"] = {"@type": "Place", "name": "Teatro", "address": "La Laguna"}
    return data


def page_with(events: list[dict[str, Any]], head: str = "") -> str:
    graph = json.dumps({"@context": "https://schema.org", "@graph": events})
    script = f'<script type="application/ld+json">{graph}</script>'
    return f"<html><head>{head}</head><body>{script}</body></html>"


def soon(days: int) -> datetime:
    return (NOW + timedelta(days=days)).replace(hour=20, minute=0)


@respx.mock
def test_a_healthy_page_is_scored_and_suggested_as_verified(make_ctx: MakeCtx) -> None:
    robots()
    html(page_with([event_ld(f"Concierto {n}", soon(2 + n)) for n in range(4)]))

    report = validate_source(client(make_ctx), URL, now=NOW)

    assert report.reachable and report.http_status == 200
    assert report.content_type == "text/html; charset=utf-8" and report.kind == "html"
    assert report.robots == "allowed"
    assert report.events_found == 4 and report.strategy == "jsonld"
    assert report.quality == 1.0
    assert report.in_next_30_days == 4
    assert report.terms == "unknown" and report.reliability is None
    assert report.suggested_mode == "html" and report.suggested_state == "verified"
    assert 60 <= report.score <= 100
    assert report.as_dict()["url"] == URL


@respx.mock
def test_quality_is_the_share_of_events_with_title_date_and_place(make_ctx: MakeCtx) -> None:
    robots()
    events = [event_ld("Con lugar", soon(2)), event_ld("Sin lugar", soon(3), place=False)]
    html(page_with(events))
    report = validate_source(client(make_ctx), URL, now=NOW)
    assert report.events_found == 2 and report.quality == 0.5
    assert report.suggested_state == "candidate"  # below 60% quality: a person should look


@respx.mock
def test_events_only_outside_the_next_30_days_are_not_enough(make_ctx: MakeCtx) -> None:
    robots()
    html(page_with([event_ld("Lejano", soon(90)), event_ld("Otro", soon(95))]))
    report = validate_source(client(make_ctx), URL, now=NOW)
    assert report.events_found == 2 and report.in_next_30_days == 0
    assert report.suggested_state == "candidate"


@respx.mock
def test_target_week_coverage_is_counted(make_ctx: MakeCtx) -> None:
    robots()
    html(
        page_with([event_ld("Pronto", soon(1)), event_ld("Semana", datetime(2026, 10, 21, 20, 0))])
    )
    report = validate_source(client(make_ctx), URL, now=NOW)  # NOW is Thu 15 Oct: week = 19..25
    assert report.in_target_week == 1 and report.in_next_30_days == 2


@respx.mock
def test_a_declared_licence_is_reported(make_ctx: MakeCtx) -> None:
    robots()
    head = '<link rel="license" href="https://creativecommons.org/licenses/by/4.0/">'
    html(page_with([event_ld("Con licencia", soon(2))], head))
    report = validate_source(client(make_ctx), URL, now=NOW)
    assert report.terms == "declared: https://creativecommons.org/licenses/by/4.0/"
    plain = validate_source(client(make_ctx), URL, now=NOW)
    assert report.score >= plain.score


@respx.mock
def test_history_gives_the_reliability(make_ctx: MakeCtx) -> None:
    robots()
    html(page_with([event_ld("Uno", soon(2))]))
    with Database(":memory:") as db:
        for n, status in enumerate(["succeeded", "succeeded", "succeeded", "failed"], start=1):
            db.record_source_run(
                n, "s", NOW, status=status, source_status="ok", events_found=5,
                events_in_window=5, duration_s=1.0, error=None, strategy="blocks",
                extraction_ok=True, listing_hash="h", anomalies=[],
            )  # fmt: skip
        history: list[SourceRun] = db.recent_source_runs("s", 10)
    report = validate_source(client(make_ctx), URL, now=NOW, history=history)
    assert report.reliability is not None
    assert report.reliability["runs"] == 4 and report.reliability["error_rate"] == 0.25


@respx.mock
def test_robots_txt_disallow_stops_before_the_page_is_fetched(make_ctx: MakeCtx) -> None:
    robots("User-agent: *\nDisallow: /\n")
    page = html("<html></html>")
    report = validate_source(client(make_ctx), URL, now=NOW)
    assert page.call_count == 0
    assert report.robots == "disallowed" and report.score == 0
    assert report.suggested_mode == "excluded" and report.suggested_state == "rejected"


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
def test_access_denied_is_never_bypassed(make_ctx: MakeCtx, status: int) -> None:
    robots()
    route = html("denied", status=status)
    report = validate_source(client(make_ctx), URL, now=NOW)
    assert route.call_count == 1 and not report.reachable
    assert report.http_status == status and report.score == 0
    assert report.suggested_mode == "requires_authorization"
    assert report.suggested_state == "rejected"


@respx.mock
def test_a_challenge_page_counts_as_blocked(make_ctx: MakeCtx) -> None:
    robots()
    html(
        "<html><head><title>Just a moment...</title></head>"
        "<body>cf-browser-verification</body></html>"
    )
    report = validate_source(client(make_ctx), URL, now=NOW)
    assert report.suggested_mode == "requires_authorization" and report.score == 0


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 503])
def test_temporary_trouble_leaves_the_candidate_as_it_is(make_ctx: MakeCtx, status: int) -> None:
    robots()
    html("later", status=status)
    report = validate_source(client(make_ctx), URL, now=NOW)
    assert not report.reachable and report.http_status == status
    assert report.suggested_state == "candidate" and report.suggested_mode is None
    assert report.transient and report.error is not None and str(status) in report.error


@respx.mock
def test_a_pdf_or_an_empty_page_is_manual_only(make_ctx: MakeCtx) -> None:
    robots()
    html("%PDF-1.4", ctype="application/pdf")
    pdf = validate_source(client(make_ctx), URL, now=NOW)
    assert pdf.kind == "other" and pdf.events_found == 0
    assert pdf.suggested_mode == "manual_only" and pdf.suggested_state == "candidate"

    html("<html><body><p>Bienvenidos</p></body></html>")
    empty = validate_source(client(make_ctx), URL, now=NOW)
    assert empty.kind == "html" and empty.events_found == 0
    assert empty.suggested_mode == "manual_only"


ICS = (
    "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//EN\nBEGIN:VEVENT\nUID:a@b\nDTSTAMP:20261001T100000Z\n"
    f"DTSTART;TZID=Atlantic/Canary:{soon(3):%Y%m%dT%H%M%S}\nSUMMARY:Verbena\n"
    "LOCATION:Plaza\\, Adeje\nEND:VEVENT\nEND:VCALENDAR\n"
)


@respx.mock
def test_an_ical_feed_is_read_as_a_feed(make_ctx: MakeCtx) -> None:
    robots()
    html(ICS, ctype="text/calendar")
    report = validate_source(client(make_ctx), URL, now=NOW)
    assert report.kind == "ical" and report.events_found == 1 and report.strategy == "ical"
    assert report.suggested_mode == "ical" and report.quality == 1.0


@respx.mock
def test_a_private_address_is_refused_without_a_request(make_ctx: MakeCtx) -> None:
    report = validate_source(client(make_ctx), "http://127.0.0.1/agenda", now=NOW)
    assert respx.calls.call_count == 0
    assert report.suggested_state == "rejected" and report.score == 0


@respx.mock
@pytest.mark.parametrize(
    ("url", "mode", "state"),
    [
        ("https://www.facebook.com/grupo", "requires_authorization", "rejected"),
        ("https://www.facebook.com/groups/1", "excluded", "rejected"),
        ("https://t.me/canal", "manual_only", "rejected"),
        ("https://www.eventbrite.es/e/x-1", "import_url", "candidate"),
    ],
)
def test_social_platforms_are_classified_without_fetching(
    make_ctx: MakeCtx, url: str, mode: str, state: str
) -> None:
    report = validate_source(client(make_ctx), url, now=NOW)
    assert respx.calls.call_count == 0
    assert (report.suggested_mode, report.suggested_state) == (mode, state)
    assert report.kind == "social"


@respx.mock
def test_a_configured_source_is_validated_with_its_own_adapter(make_ctx: MakeCtx) -> None:
    respx.get("https://feeds.example.org/events.ics").mock(
        return_value=httpx.Response(200, text=ICS, headers={"content-type": "text/calendar"})
    )
    source = SourceConfig(
        id="ics1", name="Ics", adapter="ical", url="https://feeds.example.org/events.ics",
        license="CC-BY",
    )  # fmt: skip
    report = validate_source(client(make_ctx), source.url, now=NOW, source=source)
    assert report.source_id == "ics1" and report.events_found == 1
    assert report.terms == "declared: CC-BY" and report.suggested_mode == "ical"


@respx.mock
def test_a_licence_field_that_says_unknown_is_not_a_declared_licence(make_ctx: MakeCtx) -> None:
    respx.get("https://feeds.example.org/events.ics").mock(
        return_value=httpx.Response(200, text=ICS, headers={"content-type": "text/calendar"})
    )
    source = SourceConfig(
        id="ics1", name="Ics", adapter="ical", url="https://feeds.example.org/events.ics",
        license="unknown – ToS not checked",
    )  # fmt: skip
    report = validate_source(client(make_ctx), source.url, now=NOW, source=source)
    assert report.terms == "unknown"


def test_the_report_is_plain_data() -> None:
    report = ValidationReport(url=URL)
    data = report.as_dict()
    assert data["url"] == URL and data["score"] == 0 and data["notes"] == []
    json.dumps(data)


RSS = (
    "<?xml version='1.0'?><rss version='2.0'><channel><title>Agenda</title><item>"
    "<guid>1</guid><title>Concierto en Adeje</title><link>https://agenda.example.org/e/1</link>"
    f"<pubDate>Sat, {soon(3):%d %b %Y} 20:00:00 +0000</pubDate>"
    "<description>Concierto en Adeje</description></item></channel></rss>"
)
ATOM = "<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><title>Agenda</title></feed>"


@respx.mock
@pytest.mark.parametrize(
    ("body", "ctype"),
    [(RSS, "application/rss+xml"), (RSS, "text/xml"), (ATOM, "application/atom+xml")],
)
def test_a_sniffed_feed_suggests_the_rss_mode(make_ctx: MakeCtx, body: str, ctype: str) -> None:
    robots()
    html(body, ctype=ctype)
    report = validate_source(client(make_ctx), URL, now=NOW)
    assert report.kind == "feed" and report.suggested_mode == "rss"


@respx.mock
@pytest.mark.parametrize(
    ("adapter", "mode"),
    [("web", "html"), ("ical", "ical"), ("rss", "rss"), ("ckan", "api_public")],
)
def test_the_mode_of_a_configured_source_follows_its_adapter(
    make_ctx: MakeCtx, monkeypatch: pytest.MonkeyPatch, adapter: str, mode: str
) -> None:
    from canarias_events import validation
    from canarias_events.models import Event
    from canarias_events.sources.base import SourceAdapter, SourceFetchResult

    class Fake(SourceAdapter):
        source_type = "fake"

        def fetch(self, ctx: FetchContext) -> SourceFetchResult:
            result = SourceFetchResult(records_received=1)
            result.events.append(
                Event(title="E", start_datetime=soon(2), municipality="Adeje", source_name="s")
            )
            return result

    monkeypatch.setattr(validation, "create_adapter", lambda cfg: Fake(cfg))
    source = SourceConfig(id="s1", name="S", adapter=adapter, url="https://feeds.example.org/x")
    report = validate_source(client(make_ctx), source.url, now=NOW, source=source)
    assert report.events_found == 1 and report.suggested_mode == mode


@respx.mock
def test_an_unreadable_robots_txt_is_a_transient_error(make_ctx: MakeCtx) -> None:
    robots(status=503)
    page = html("<html></html>")
    report = validate_source(client(make_ctx), URL, now=NOW)
    assert page.call_count == 0 and report.robots == "unavailable"
    assert report.transient and report.error is not None and "robots" in report.error


@respx.mock
def test_a_timeout_is_a_transient_error(make_ctx: MakeCtx) -> None:
    robots()
    respx.get(URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    report = validate_source(client(make_ctx), URL, now=NOW)
    assert report.transient and report.error is not None and not report.reachable


@respx.mock
def test_denied_access_is_a_definitive_answer_not_a_transient_error(make_ctx: MakeCtx) -> None:
    robots()
    html("no", status=403)
    report = validate_source(client(make_ctx), URL, now=NOW)
    assert not report.transient and report.error is None and report.suggested_state == "rejected"


@respx.mock
@pytest.mark.parametrize("failure", [ValueError("bad calendar"), KeyError("x")])
def test_a_parser_error_becomes_an_error_in_the_report(
    make_ctx: MakeCtx, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    from canarias_events import validation

    def explode(self: object, body: bytes, window: object) -> None:
        raise failure

    monkeypatch.setattr(validation.IcalAdapter, "read", explode)
    robots()
    html(ICS, ctype="text/calendar")
    report = validate_source(client(make_ctx), URL, now=NOW)
    assert report.error is not None and not report.transient
    assert report.reachable and report.events_found == 0
    assert report.suggested_state == "candidate"


@respx.mock
def test_an_invalid_feed_payload_becomes_an_error_in_the_report(
    make_ctx: MakeCtx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from canarias_events import validation

    def reject(self: object, body: bytes) -> None:
        raise InvalidPayload("not a feed")

    monkeypatch.setattr(validation.RssAdapter, "read", reject)
    robots()
    html(RSS, ctype="application/rss+xml")
    report = validate_source(client(make_ctx), URL, now=NOW)
    assert report.error is not None and "not a feed" in report.error and not report.transient
    assert report.suggested_state == "candidate"


@respx.mock
def test_a_broken_adapter_configuration_becomes_an_error(
    make_ctx: MakeCtx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from canarias_events import validation

    def refuse(cfg: SourceConfig) -> None:
        raise ValueError("invalid params")

    monkeypatch.setattr(validation, "create_adapter", refuse)
    source = SourceConfig(id="s1", name="S", adapter="web", url="https://feeds.example.org/x")
    report = validate_source(client(make_ctx), source.url, now=NOW, source=source)
    assert report.error is not None and "invalid params" in report.error
