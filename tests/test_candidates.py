"""Source candidates: storage, URL helpers and what discovery turns into candidates."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx

from canarias_events.candidates import (
    candidate_id,
    candidates_from_report,
    extract_candidate_links,
    registrable_domain,
)
from canarias_events.database import Database
from canarias_events.discovery import discover_links
from canarias_events.social import classify_social_url
from canarias_events.sources.base import FetchContext, SourceConfig
from tests.conftest import NOW

MakeCtx = Callable[..., FetchContext]

# --------------------------------------------------------------------------- store


def test_a_new_candidate_is_stored_as_candidate_and_never_enabled() -> None:
    with Database(":memory:") as db:
        assert db.add_candidate("https://example.org/agenda", "web", "manual", NOW) is True
        [row] = db.list_candidates()
        assert row.url == "https://example.org/agenda"
        assert row.verification_state == "candidate" and row.kind == "web"
        assert row.discovered_via == "manual" and row.id == candidate_id(row.url)
        assert db.count("sources") == 0  # nothing reaches the sources table


def test_adding_an_existing_url_changes_nothing() -> None:
    with Database(":memory:") as db:
        db.add_candidate("https://example.org/agenda", "web", "manual", NOW)
        db.update_candidate(
            candidate_id("https://example.org/agenda"), state="verified", mode="rss",
            score=80, report={"x": 1}, now=NOW,
        )  # fmt: skip
        assert db.add_candidate("https://example.org/agenda", "web", "link:other", NOW) is False
        [row] = db.list_candidates()
        assert row.verification_state == "verified" and row.discovered_via == "manual"


def test_lookup_by_id_or_url_and_update() -> None:
    with Database(":memory:") as db:
        url = "https://example.org/agenda"
        db.add_candidate(url, "web", "manual", NOW)
        by_url, by_id = db.get_candidate(url), db.get_candidate(candidate_id(url))
        assert by_url is not None and by_url == by_id
        assert db.get_candidate("nope") is None
        db.update_candidate(
            by_url.id, state="rejected", mode="excluded", score=0, report={}, now=NOW
        )
        updated = db.get_candidate(url)
        assert updated is not None
        assert (updated.verification_state, updated.acquisition_mode) == ("rejected", "excluded")
        assert updated.last_score == 0 and updated.last_validated_at == NOW.isoformat()


def test_touching_a_candidate_keeps_its_state_mode_and_score() -> None:
    with Database(":memory:") as db:
        url = "https://example.org/agenda"
        db.add_candidate(url, "web", "manual", NOW)
        cid = candidate_id(url)
        db.update_candidate(cid, state="verified", mode="html", score=80, report={"a": 1}, now=NOW)
        later = NOW.replace(hour=NOW.hour - 1)
        db.touch_candidate(cid, report={"error": "HTTP 503"}, now=later)
        row = db.get_candidate(url)
        assert row is not None
        assert (row.verification_state, row.acquisition_mode, row.last_score) == (
            "verified", "html", 80,
        )  # fmt: skip
        assert row.last_validated_at == later.isoformat()
        assert row.report == {"error": "HTTP 503"}


def test_migrating_an_older_database_adds_the_table(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "old.sqlite"
    with Database(path) as db:
        db.conn.execute("DROP TABLE source_candidates")
        db.conn.execute("PRAGMA user_version = 3")
        db.conn.commit()
    with Database(path) as db:
        assert db.add_candidate("https://example.org/x", "web", "manual", NOW)
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] >= 4


# --------------------------------------------------------------------------- url helpers


def test_candidate_ids_are_readable_and_distinct() -> None:
    a = candidate_id("https://www.teatroleal.es/programacion/")
    b = candidate_id("https://www.teatroleal.es/programacion/?page=2")
    assert a.startswith("teatroleal-es-programacion-") and a != b
    assert candidate_id("https://www.teatroleal.es/programacion/") == a


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("www.teatroleal.es", "teatroleal.es"),
        ("agenda.santacruzdetenerife.es", "santacruzdetenerife.es"),
        ("www.gobiernodecanarias.org", "gobiernodecanarias.org"),
        ("sede.ayto.com.es", "ayto.com.es"),
        ("localhost", "localhost"),
        ("127.0.0.1", "127.0.0.1"),
    ],
)
def test_registrable_domain_is_a_small_heuristic(host: str, expected: str) -> None:
    assert registrable_domain(host) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.facebook.com/groups/123", ("facebook", "excluded")),
        ("https://www.facebook.com/teatroleal", ("facebook", "requires_authorization")),
        ("https://fb.me/e/abc", ("facebook", "requires_authorization")),
        ("https://www.instagram.com/teatroleal/", ("instagram", "requires_authorization")),
        ("https://www.meetup.com/some-group/", ("meetup", "requires_authorization")),
        ("https://www.eventbrite.es/e/gala-tickets-1", ("eventbrite", "import_url")),
        ("https://t.me/canalagenda", ("telegram", "manual_only")),
        ("https://bsky.app/profile/agenda.bsky.social", ("bluesky", "api_public")),
        ("https://mastodon.example/@agenda", ("mastodon", "rss")),
        ("https://www.youtube.com/@canal", ("youtube", "rss")),
    ],
)
def test_social_urls_are_classified_by_platform(url: str, expected: tuple[str, str]) -> None:
    assert classify_social_url(url) == expected


def test_ordinary_sites_are_not_social() -> None:
    assert classify_social_url("https://www.teatroleal.es/programacion/") is None


# --------------------------------------------------------------------------- links

PAGE = """<html><body>
<a href="/agenda/">Agenda cultural</a>
<a href="https://www.example.org/eventos/otoño">Eventos de otoño</a>
<a href="https://cultura.example.org/programacion">Programación</a>
<a href="https://other.test/agenda">Agenda de otro</a>
<a href="https://official.test/actividades">Actividades</a>
<a href="/contacto">Contacto</a>
<a href="/agenda/">Agenda cultural otra vez</a>
<a href="mailto:a@example.org">Agenda por correo</a>
<a href="javascript:void(0)">Agenda js</a>
<a href="https://www.facebook.com/example">Agenda en Facebook</a>
<a href="/agenda/ya-configurada">Agenda</a>
</body></html>"""


def test_links_stay_on_the_same_domain_or_listed_official_domains() -> None:
    links = extract_candidate_links(
        PAGE, "https://www.example.org/", official_domains={"official.test"},
        known={"https://www.example.org/agenda/ya-configurada"}, limit=10,
    )  # fmt: skip
    assert links == [
        "https://www.example.org/agenda/",
        "https://www.example.org/eventos/otoño",
        "https://cultura.example.org/programacion",
        "https://official.test/actividades",
    ]


def test_a_malformed_href_is_skipped_not_fatal() -> None:
    html = (
        '<a href="http://[::1/agenda">Agenda rota</a>'
        '<a href="https://[2001:db8::/eventos">Eventos rotos</a>'
        '<a href="/agenda/buena">Agenda</a>'
    )
    links = extract_candidate_links(
        html, "https://www.example.org/", official_domains=set(), known=set(), limit=10
    )
    assert links == ["https://www.example.org/agenda/buena"]


def test_link_count_is_bounded() -> None:
    links = extract_candidate_links(
        PAGE, "https://www.example.org/", official_domains=set(), known=set(), limit=2
    )
    assert len(links) == 2


# --------------------------------------------------------------------------- report


def test_positive_discovery_results_become_candidates() -> None:
    report = {
        "results": [
            {
                "endpoint": "https://datos.tenerife.es/ckan/api/3/action/package_search?q=agenda",
                "kind": "ckan_search",
                "needs_manual_check": True,
                "datasets": [{"name": "agenda-cultural", "title": "Agenda"}, {"name": None}],
            },
            {
                "endpoint": "https://datos.gob.es/apidata/catalog/dataset/title/eventos.json",
                "kind": "datos_gob_es",
                "needs_manual_check": True,
                "datasets": [{"url": "https://datos.gob.es/catalogo/a1", "title": "Eventos"}],
            },
            {
                "endpoint": "https://probe.test/wp-json/tribe/events/v1/events?per_page=1",
                "kind": "tribe_events",
                "needs_manual_check": True,
                "domain": "probe.test",
            },
            {"endpoint": "https://probe.test/?ical=1", "kind": "ical", "needs_manual_check": False},
        ]
    }
    found = candidates_from_report(report)
    assert [(c.url, c.discovered_via) for c in found] == [
        ("https://datos.tenerife.es/ckan/dataset/agenda-cultural", "ckan_search"),
        ("https://datos.gob.es/catalogo/a1", "datos_gob_es"),
        ("https://probe.test/wp-json/tribe/events/v1/events?per_page=1", "tribe_events"),
    ]


# --------------------------------------------------------------------------- link discovery


def _serve(
    host: str, body: str | None, *, robots: str | None = None, ctype: str = "text/html"
) -> None:
    respx.get(f"https://{host}/robots.txt").mock(
        return_value=httpx.Response(200, text=robots) if robots is not None else httpx.Response(404)
    )
    if body is not None:
        respx.get(f"https://{host}/").mock(
            return_value=httpx.Response(200, text=body, headers={"content-type": ctype})
        )


def _source(source_id: str, url: str, **overrides: object) -> SourceConfig:
    data: dict[str, object] = {"id": source_id, "name": source_id, "adapter": "web", "url": url}
    data.update(overrides)
    return SourceConfig.model_validate(data)


@respx.mock
def test_links_of_verified_sources_become_candidates(make_ctx: MakeCtx) -> None:
    _serve("www.example.org", PAGE)
    sources = [
        _source("web1", "https://www.example.org/"),
        _source("off", "https://www.example.org/", enabled=False),
        _source("api", "https://api.test/events", adapter="ckan"),
    ]
    found, notes = discover_links(
        make_ctx().client, sources, known=set(), official_domains={"official.test"}
    )
    assert [c.url for c in found] == [
        "https://www.example.org/agenda/",
        "https://www.example.org/eventos/otoño",
        "https://cultura.example.org/programacion",
        "https://official.test/actividades",
        "https://www.example.org/agenda/ya-configurada",
    ]
    assert {c.discovered_via for c in found} == {"link:web1"} and notes == []


@respx.mock
def test_link_discovery_honours_robots_and_content_type(make_ctx: MakeCtx) -> None:
    _serve("www.example.org", PAGE, robots="User-agent: *\nDisallow: /\n")
    found, notes = discover_links(
        make_ctx().client, [_source("web1", "https://www.example.org/")], known=set(),
        official_domains=set(),
    )  # fmt: skip
    assert found == [] and any("robots.txt" in n for n in notes)

    respx.clear()
    _serve("www.example.org", "%PDF", ctype="application/pdf")
    found, notes = discover_links(
        make_ctx().client, [_source("web1", "https://www.example.org/")], known=set(),
        official_domains=set(),
    )  # fmt: skip
    assert found == []


@respx.mock
def test_link_discovery_is_bounded_and_survives_failures(make_ctx: MakeCtx) -> None:
    for n in range(15):
        _serve(f"s{n}.example.org", f'<a href="/agenda/{n}">Agenda</a>')
    respx.get("https://s0.example.org/").mock(return_value=httpx.Response(500))
    sources = [_source(f"s{n}", f"https://s{n}.example.org/") for n in range(15)]
    found, notes = discover_links(
        make_ctx().client, sources, known=set(), official_domains=set(), max_sources=5
    )
    assert len(found) == 4 and len(notes) == 1  # 5 sources tried, one failed
