"""One-off imports: ``import-url`` pages and organizer submissions (always reviewed first)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from canarias_events import manual_import, social
from canarias_events.database import Database
from canarias_events.date_range import TZ
from canarias_events.http import InvalidPayload, UnsafeUrlError
from canarias_events.manual_import import (
    IMPORT_URL_SOURCE,
    ORGANIZER_SOURCE,
    RefusedUrl,
    hold_for_review,
    import_url,
    publish_now,
)
from canarias_events.models import Event
from canarias_events.pipeline import accept_quarantine
from canarias_events.sources.base import FetchContext
from canarias_events.submissions import SubmissionError, load_submission
from tests.conftest import NOW

MakeCtx = Callable[..., FetchContext]
URL = "https://www.eventbrite.es/e/concierto-de-gala-tickets-123456"


def page(body: str, url: str = URL, content_type: str = "text/html; charset=utf-8") -> respx.Route:
    return respx.get(url).mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": content_type})
    )


def robots(host: str = "www.eventbrite.es", body: str | None = None) -> respx.Route:
    response = httpx.Response(200, text=body) if body is not None else httpx.Response(404)
    return respx.get(f"https://{host}/robots.txt").mock(return_value=response)


def jsonld(**fields: Any) -> str:
    data: dict[str, Any] = {
        "@context": "https://schema.org", "@type": "Event", "name": "Concierto de gala",
        "startDate": "2026-10-24T20:30:00+01:00",
    }  # fmt: skip
    data.update(fields)
    return (
        f'<html><body><script type="application/ld+json">{json.dumps(data)}</script></body></html>'
    )


# --------------------------------------------------------------------------- import_url


@respx.mock
def test_a_public_event_page_becomes_a_reviewable_event(make_ctx: MakeCtx) -> None:
    robots()
    page(jsonld(location={"@type": "Place", "name": "Teatro Leal", "address": "La Laguna"}))

    outcome = import_url(make_ctx().client, URL, now=NOW)

    assert outcome.status == "ok" and outcome.warnings == []
    [event] = outcome.events
    assert event.title == "Concierto de gala"
    assert event.start_datetime == datetime(2026, 10, 24, 20, 30, tzinfo=TZ)
    assert event.venue_name == "Teatro Leal"
    assert event.source_type == "import_url" and event.source_url == URL
    assert event.extra["provenance"] == "import_url"
    assert event.extra["source_page"] == URL
    assert event.official_event_url == URL  # a single event: the page is its own page
    assert event.price_min is None and event.description is None  # nothing is invented


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/events/123456",
        "https://m.facebook.com/events/123456",
        "https://fb.me/e/abc",
        "https://web.facebook.com/groups/1/events",
        "https://www.instagram.com/p/abc/",
        "https://instagr.am/p/abc/",
        "https://l.facebook.com/l.php?u=x",
    ],
)
@respx.mock
def test_facebook_and_instagram_are_refused_without_any_request(
    make_ctx: MakeCtx, url: str
) -> None:
    with pytest.raises(RefusedUrl, match="submit-event"):
        import_url(make_ctx().client, url, now=NOW)
    assert respx.calls.call_count == 0


@pytest.mark.parametrize("url", ["ftp://example.org/x", "file:///etc/passwd", "not a url", ""])
def test_only_http_and_https_are_accepted(make_ctx: MakeCtx, url: str) -> None:
    with pytest.raises(ValueError, match="http"):
        import_url(make_ctx().client, url, now=NOW)


@respx.mock
def test_private_addresses_are_refused_by_the_ssrf_guard(make_ctx: MakeCtx) -> None:
    with pytest.raises(UnsafeUrlError):
        import_url(make_ctx().client, "http://127.0.0.1/evento", now=NOW)
    assert respx.calls.call_count == 0


@respx.mock
def test_robots_txt_is_respected(make_ctx: MakeCtx) -> None:
    robots(body="User-agent: *\nDisallow: /e/\n")
    route = page(jsonld())
    outcome = import_url(make_ctx().client, URL, now=NOW)
    assert outcome.status == "robots_disallowed" and outcome.events == []
    assert route.call_count == 0


@respx.mock
def test_a_pdf_is_not_an_event_page(make_ctx: MakeCtx) -> None:
    robots()
    page("%PDF-1.4", content_type="application/pdf")
    with pytest.raises(InvalidPayload):
        import_url(make_ctx().client, URL, now=NOW)


@respx.mock
def test_a_forbidden_page_is_blocked_and_never_bypassed(make_ctx: MakeCtx) -> None:
    robots()
    route = respx.get(URL).mock(return_value=httpx.Response(403))
    outcome = import_url(make_ctx().client, URL, now=NOW)
    assert outcome.status == "blocked" and outcome.events == []
    assert route.call_count == 1


@respx.mock
def test_a_page_without_event_data_yields_nothing(make_ctx: MakeCtx) -> None:
    robots()
    page("<html><body><p>Bienvenidos a nuestra web</p></body></html>")
    outcome = import_url(make_ctx().client, URL, now=NOW)
    assert outcome.status == "needs_manual_review" and outcome.events == []


@respx.mock
def test_a_listing_page_is_capped(make_ctx: MakeCtx) -> None:
    robots()
    items = [
        {"@type": "Event", "name": f"Evento {n}", "startDate": f"2026-11-{1 + n:02d}"}
        for n in range(30)
    ]
    graph = json.dumps({"@context": "https://schema.org", "@graph": items})
    body = f'<script type="application/ld+json">{graph}</script>'
    page(body)
    outcome = import_url(make_ctx().client, URL, now=NOW)
    assert len(outcome.events) == 25 and any("first 25" in w for w in outcome.warnings)
    assert all(e.official_event_url is None for e in outcome.events)  # a listing is no event page


# --------------------------------------------------------------------------- review hold


def sample(title: str = "Concierto", event_id: str = "e1") -> Event:
    return Event(
        title=title, start_datetime=datetime(2026, 10, 24, 20, 0, tzinfo=TZ),
        municipality="Adeje", island="Tenerife", source_name="x", source_event_id=event_id,
        source_type="import_url",
    )  # fmt: skip


def test_held_events_wait_for_review_and_are_not_published() -> None:
    with Database(":memory:") as db:
        assert hold_for_review(db, IMPORT_URL_SOURCE, [sample()], NOW) == 1
        assert db.count("events") == 0
        [item] = db.quarantine_pending("import_url")
        assert item.reason == "needs_review" and item.event.title == "Concierto"


def test_holding_does_not_supersede_earlier_pending_events_or_repeat_them() -> None:
    with Database(":memory:") as db:
        hold_for_review(db, IMPORT_URL_SOURCE, [sample("Uno", "e1")], NOW)
        assert hold_for_review(db, IMPORT_URL_SOURCE, [sample("Dos", "e2")], NOW) == 1
        assert hold_for_review(db, IMPORT_URL_SOURCE, [sample("Uno", "e1")], NOW) == 0
        assert [i.event.title for i in db.quarantine_pending("import_url")] == ["Uno", "Dos"]


def test_accepting_publishes_through_dedup_and_leaves_no_anomaly_baseline() -> None:
    with Database(":memory:") as db:
        hold_for_review(db, IMPORT_URL_SOURCE, [sample("Uno", "e1")], NOW)
        assert accept_quarantine(db, "import_url", now=NOW) == 1
        assert [e.title for e in db.all_events()] == ["Uno"]
        assert db.recent_source_runs("import_url", 5) == []  # a manual import is no run history


def test_publish_now_goes_straight_through_dedup() -> None:
    with Database(":memory:") as db:
        stats = publish_now(db, ORGANIZER_SOURCE, [sample()], NOW)
        assert stats["new"] == 1 and db.count("events") == 1
        again = publish_now(db, ORGANIZER_SOURCE, [sample()], NOW)
        assert again["unchanged"] == 1 and db.count("events") == 1


# --------------------------------------------------------------------------- submissions


def write_json(tmp_path: Path, data: Any, name: str = "event.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


GOOD: dict[str, Any] = {
    "title": "Taller de cerámica",
    "start": "2026-10-24T18:00:00",
    "end": "2026-10-24T20:00:00",
    "organizer_name": "Asociación Alfareros",
    "municipality": "La Laguna",
    "venue_name": "Casa de la Cultura",
    "url": "https://alfareros.example.org/taller",
    "price_min": 10,
    "currency": "EUR",
}


def test_a_valid_organizer_event_is_mapped_without_inventing_anything(tmp_path: Path) -> None:
    [event] = load_submission(write_json(tmp_path, GOOD), organizer=None, now=NOW)
    assert event.title == "Taller de cerámica"
    assert event.start_datetime == datetime(2026, 10, 24, 18, 0, tzinfo=TZ)
    assert event.end_datetime == datetime(2026, 10, 24, 20, 0, tzinfo=TZ)
    assert event.municipality == "San Cristóbal de La Laguna" and event.island == "Tenerife"
    assert event.official_event_url == "https://alfareros.example.org/taller"
    assert event.organizer_name == "Asociación Alfareros"
    assert event.source_type == "organizer_submission"
    assert event.source_name == "Organizer: Asociación Alfareros"
    assert event.source_event_id is not None and event.source_event_id.startswith("org:")
    assert event.extra["provenance"] == "organizer_submission"
    assert event.price_max is None and event.description is None
    assert event.raw_data["title"] == "Taller de cerámica"


def test_the_source_event_id_is_stable_or_the_organizers_own(tmp_path: Path) -> None:
    [a] = load_submission(write_json(tmp_path, GOOD), organizer=None, now=NOW)
    [b] = load_submission(write_json(tmp_path, GOOD), organizer=None, now=NOW)
    assert a.source_event_id == b.source_event_id
    [own] = load_submission(write_json(tmp_path, {**GOOD, "id": "taller-2026"}), None, NOW)
    assert own.source_event_id == "taller-2026"


def test_a_date_only_start_is_an_all_day_event(tmp_path: Path) -> None:
    data = {**GOOD, "start": "2026-10-24", "end": None}
    [event] = load_submission(write_json(tmp_path, data), organizer=None, now=NOW)
    assert event.all_day is True and event.start_date == date(2026, 10, 24)
    assert event.start_datetime is None


def test_wrapper_and_array_forms_are_accepted(tmp_path: Path) -> None:
    other = {**GOOD, "title": "Otro"}
    assert len(load_submission(write_json(tmp_path, [GOOD, other]), None, NOW)) == 2
    assert len(load_submission(write_json(tmp_path, {"events": [GOOD, other]}), None, NOW)) == 2


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"title": ""}, "title"),
        ({"start": "mañana"}, "start"),
        ({"organizer_name": None}, "organizer_name"),
        ({"surprise": 1}, "surprise"),
        ({"municipality": "Atlantis"}, "municipality"),
        ({"island": "Narnia"}, "island"),
        ({"municipality": "Adeje", "island": "La Palma"}, "island"),
        ({"url": "javascript:alert(1)"}, "url"),
        ({"end": "2026-10-23T10:00:00"}, "end"),
        ({"price_min": -1}, "price_min"),
        ({"contact_email": "not an email"}, "contact_email"),
    ],
)
def test_invalid_submissions_are_rejected_with_the_field_named(
    tmp_path: Path, change: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(SubmissionError) as err:
        load_submission(write_json(tmp_path, {**GOOD, **change}), organizer=None, now=NOW)
    assert any(fragment in message for message in err.value.errors)


def test_one_bad_event_rejects_the_whole_file(tmp_path: Path) -> None:
    with pytest.raises(SubmissionError) as err:
        load_submission(write_json(tmp_path, [GOOD, {**GOOD, "title": ""}]), None, NOW)
    assert err.value.errors[0].startswith("event 2:")


def test_broken_or_unknown_files_are_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    with pytest.raises(SubmissionError, match="JSON"):
        load_submission(bad, None, NOW)
    other = tmp_path / "event.txt"
    other.write_text("x", encoding="utf-8")
    with pytest.raises(SubmissionError, match=r"\.json or \.ics"):
        load_submission(other, None, NOW)
    with pytest.raises(SubmissionError, match="cannot read"):
        load_submission(tmp_path / "missing.json", None, NOW)


ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:abc-1@alfareros.example.org
DTSTAMP:20261001T100000Z
DTSTART;TZID=Atlantic/Canary:20261024T180000
DTEND;TZID=Atlantic/Canary:20261024T200000
SUMMARY:Taller de cerámica
LOCATION:Casa de la Cultura\\, Adeje
URL:https://alfareros.example.org/taller
END:VEVENT
END:VCALENDAR
"""


def test_an_ics_file_needs_the_organizer_name(tmp_path: Path) -> None:
    path = tmp_path / "event.ics"
    path.write_text(ICS, encoding="utf-8")
    with pytest.raises(SubmissionError, match="--organizer"):
        load_submission(path, organizer=None, now=NOW)
    [event] = load_submission(path, organizer="Asociación Alfareros", now=NOW)
    assert event.title == "Taller de cerámica"
    assert event.start_datetime == datetime(2026, 10, 24, 18, 0, tzinfo=TZ)
    assert event.municipality == "Adeje" and event.island == "Tenerife"
    assert event.source_type == "organizer_submission"
    assert event.organizer_name == "Asociación Alfareros"
    assert event.source_event_id == "abc-1@alfareros.example.org"
    assert event.extra["provenance"] == "organizer_submission"


def test_refused_hosts_are_the_social_registry_hosts() -> None:
    for host in social.REFUSED_HOSTS:
        found = social.classify_social_url(f"https://{host}/page")
        assert found is not None and found[0] in ("facebook", "instagram")
        with pytest.raises(RefusedUrl):
            manual_import.check_url(f"https://www.{host}/page")
