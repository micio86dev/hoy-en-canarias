"""Mastodon (account RSS) and Bluesky (public AppView) adapters: posts are not events."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime
from html import escape
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from canarias_events.date_range import TZ
from canarias_events.http import HttpError, InvalidPayload
from canarias_events.sources.base import FetchContext, SourceConfig
from canarias_events.sources.bluesky import BlueskyAdapter
from canarias_events.sources.mastodon import MastodonAdapter
from canarias_events.sources.registry import ADAPTERS, create_adapter
from canarias_events.sources.web import WebAdapter

MakeCtx = Callable[..., FetchContext]
FEED = "https://mastodon.example/@agenda.rss"
APPVIEW = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
PAGE = "https://teatro.example.org/evento/gala"


def mastodon_cfg(**overrides: Any) -> SourceConfig:
    data: dict[str, Any] = {
        "id": "masto_demo", "name": "Agenda (Mastodon)", "adapter": "mastodon", "url": FEED,
        "islands": ["Tenerife"], "priority": 10, "license": "public posts",
        "params": {
            "handle": "agenda@mastodon.example", "link_hosts": ["teatro.example.org"],
            "year_policy": "infer",
        },
    }  # fmt: skip
    data.update(overrides)
    return SourceConfig.model_validate(data)


def bluesky_cfg(**overrides: Any) -> SourceConfig:
    data: dict[str, Any] = {
        "id": "bsky_demo", "name": "Agenda (Bluesky)", "adapter": "bluesky", "url": APPVIEW,
        "priority": 10, "params": {
            "handle": "agenda.bsky.social", "link_hosts": ["teatro.example.org"],
            "year_policy": "infer",
        },
    }  # fmt: skip
    data.update(overrides)
    return SourceConfig.model_validate(data)


def rss(*items: tuple[str, str, str]) -> bytes:
    """Mastodon account RSS: (post id, published RFC 822, HTML content)."""
    body = "".join(
        f"<item><guid isPermaLink='true'>https://mastodon.example/@agenda/{pid}</guid>"
        f"<link>https://mastodon.example/@agenda/{pid}</link><pubDate>{published}</pubDate>"
        f"<description>{escape(html)}</description></item>"
        for pid, published, html in items
    )
    return (
        "<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel>"
        f"<title>Agenda</title><link>https://mastodon.example/@agenda</link>{body}"
        "</channel></rss>"
    ).encode()


PUBLISHED = "Wed, 14 Oct 2026 10:00:00 +0000"


def serve_feed(*items: tuple[str, str, str]) -> respx.Route:
    return respx.get(FEED).mock(return_value=httpx.Response(200, content=rss(*items)))


def robots(host: str, body: str | None = None) -> respx.Route:
    response = httpx.Response(200, text=body) if body is not None else httpx.Response(404)
    return respx.get(f"https://{host}/robots.txt").mock(return_value=response)


def event_page(name: str, start: str, url: str = PAGE) -> respx.Route:
    data = {
        "@context": "https://schema.org", "@type": "Event", "name": name, "startDate": start,
        "location": {"@type": "Place", "name": "Teatro Leal", "address": "La Laguna"},
    }  # fmt: skip
    html = (
        f'<html><body><script type="application/ld+json">{json.dumps(data)}</script></body></html>'
    )
    return respx.get(url).mock(
        return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )


def test_adapters_are_registered() -> None:
    assert ADAPTERS["mastodon"] is MastodonAdapter and ADAPTERS["bluesky"] is BlueskyAdapter
    assert isinstance(create_adapter(mastodon_cfg()), MastodonAdapter)


# --------------------------------------------------------------------------- mastodon


@respx.mock
def test_a_post_with_a_date_and_a_place_becomes_a_low_trust_event(make_ctx: MakeCtx) -> None:
    serve_feed(
        ("1", PUBLISHED, "<p>Concierto de gala en Adeje el sábado 24 de octubre a las 21:00</p>")
    )
    result = MastodonAdapter(mastodon_cfg()).fetch(make_ctx())

    assert result.records_received == 1 and result.pages == 1
    [event] = result.events
    assert event.title == "Concierto de gala en Adeje el sábado 24 de octubre a las 21:00"
    assert event.start_datetime == datetime(2026, 10, 24, 21, 0, tzinfo=TZ)
    assert event.municipality == "Adeje" and event.island == "Tenerife"
    assert event.venue_name is None and event.price_min is None  # nothing is invented
    assert event.official_event_url is None
    assert event.source_type == "mastodon" and event.source_url == FEED
    assert event.source_event_id == "https://mastodon.example/@agenda/1"
    assert event.extra["source_trust"] == "social"
    assert event.extra["post_url"] == "https://mastodon.example/@agenda/1"
    assert event.extra["year_inferred"] is True


@respx.mock
def test_a_date_only_post_is_skipped(make_ctx: MakeCtx) -> None:
    serve_feed(("1", PUBLISHED, "<p>El sábado 24 de octubre no os lo perdáis</p>"))
    result = MastodonAdapter(mastodon_cfg()).fetch(make_ctx())
    assert result.events == []
    assert [r for r, _ in result.discarded] == ["no event page or place"]


@respx.mock
def test_a_place_only_post_is_skipped(make_ctx: MakeCtx) -> None:
    serve_feed(("1", PUBLISHED, "<p>Gran concierto en Adeje, muy pronto</p>"))
    result = MastodonAdapter(mastodon_cfg()).fetch(make_ctx())
    assert result.events == []
    assert [r for r, _ in result.discarded] == ["no deterministic date"]


@respx.mock
def test_several_dates_without_an_event_page_are_ambiguous(make_ctx: MakeCtx) -> None:
    serve_feed(("1", PUBLISHED, "<p>Programa en Adeje: 24 de octubre y 31 de octubre</p>"))
    result = MastodonAdapter(mastodon_cfg()).fetch(make_ctx())
    assert result.events == []
    assert [r for r, _ in result.discarded] == ["ambiguous: several dates"]


@respx.mock
def test_a_linked_event_page_with_json_ld_supplies_the_event(make_ctx: MakeCtx) -> None:
    tag = '<a href="https://mastodon.example/tags/gala" class="mention hashtag" rel="tag">#gala</a>'
    html = f'<p>Gala el 24 de octubre, entradas <a href="{PAGE}">aquí</a> {tag}</p>'
    serve_feed(("1", PUBLISHED, html))
    robots("teatro.example.org")
    page = event_page("Gala de otoño", "2026-10-24T20:30:00+01:00")

    result = MastodonAdapter(mastodon_cfg()).fetch(make_ctx())

    assert page.call_count == 1
    [event] = result.events
    assert event.title == "Gala de otoño"
    assert event.official_event_url == PAGE
    assert event.start_datetime == datetime(2026, 10, 24, 20, 30, tzinfo=TZ)
    assert event.venue_name == "Teatro Leal"
    assert event.source_name == "Agenda (Mastodon)" and event.source_type == "mastodon"
    assert event.source_event_id == "https://mastodon.example/@agenda/1"
    assert event.extra["source_trust"] == "social"
    assert event.extra["derived_from"] == "event_page"
    assert event.source_license == "public posts"


@respx.mock
def test_a_linked_page_with_another_date_is_not_trusted(make_ctx: MakeCtx) -> None:
    serve_feed(("1", PUBLISHED, f'<p>Gala el 24 de octubre <a href="{PAGE}">info</a></p>'))
    robots("teatro.example.org")
    event_page("Gala de otoño", "2026-11-14T20:30:00+01:00")
    result = MastodonAdapter(mastodon_cfg()).fetch(make_ctx())
    assert result.events == []  # no place in the post either: nothing to publish


@respx.mock
def test_a_linked_page_that_does_not_match_falls_back_to_the_explicit_place(
    make_ctx: MakeCtx,
) -> None:
    html = f'<p>Gala en La Laguna el 24 de octubre <a href="{PAGE}">info</a></p>'
    serve_feed(("1", PUBLISHED, html))
    robots("teatro.example.org")
    event_page("Gala", "2026-11-14T20:30:00+01:00")
    [event] = MastodonAdapter(mastodon_cfg()).fetch(make_ctx()).events
    assert event.extra["derived_from"] == "post_text"
    assert event.municipality == "San Cristóbal de La Laguna"


@respx.mock
def test_robots_disallow_on_the_linked_page_is_respected(make_ctx: MakeCtx) -> None:
    html = f'<p>Gala en Adeje el 24 de octubre <a href="{PAGE}">info</a></p>'
    serve_feed(("1", PUBLISHED, html))
    robots("teatro.example.org", "User-agent: *\nDisallow: /evento/\n")
    page = event_page("Gala", "2026-10-24T20:30:00+01:00")

    result = MastodonAdapter(mastodon_cfg()).fetch(make_ctx())

    assert page.call_count == 0
    [event] = result.events  # the explicit date and place still stand
    assert event.extra["derived_from"] == "post_text"
    assert any("robots.txt" in w for w in result.warnings)


@respx.mock
@pytest.mark.parametrize(("status", "label"), [(403, "blocked"), (429, "rate_limited")])
def test_a_refused_event_page_is_reported_with_its_host_and_status(
    make_ctx: MakeCtx, status: int, label: str
) -> None:
    html = f'<p>Gala en Adeje el 24 de octubre <a href="{PAGE}">info</a></p>'
    serve_feed(("1", PUBLISHED, html))
    robots("teatro.example.org")
    respx.get(PAGE).mock(return_value=httpx.Response(status, text="no"))

    result = MastodonAdapter(mastodon_cfg()).fetch(make_ctx())

    assert [e.extra["derived_from"] for e in result.events] == ["post_text"]
    assert any("teatro.example.org" in w and label in w for w in result.warnings), result.warnings


@respx.mock
def test_an_unexpected_error_on_one_link_skips_only_that_link(
    make_ctx: MakeCtx, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad, good = "https://bad.example.org/e", PAGE
    serve_feed(
        ("1", PUBLISHED, f'<p>Gala en Adeje el 24 de octubre <a href="{bad}">x</a></p>'),
        ("2", PUBLISHED, f'<p>Gala el 24 de octubre de 2026 <a href="{good}">y</a></p>'),
    )
    robots("teatro.example.org")
    event_page("Gala de otoño", "2026-10-24T20:30:00+01:00")
    real = WebAdapter.fetch

    def flaky(self: WebAdapter, ctx: FetchContext) -> Any:
        if "bad.example.org" in self.config.url:
            raise RuntimeError("parser exploded")
        return real(self, ctx)

    monkeypatch.setattr(WebAdapter, "fetch", flaky)
    cfg = mastodon_cfg(
        params={
            "handle": "agenda@mastodon.example",
            "link_hosts": ["teatro.example.org", "bad.example.org"],
            "year_policy": "infer",
        }
    )
    result = MastodonAdapter(cfg).fetch(make_ctx())

    assert sorted(e.extra["derived_from"] for e in result.events) == ["event_page", "post_text"]
    assert any("bad.example.org" in w and "parser exploded" in w for w in result.warnings)


@respx.mock
def test_links_are_fetched_at_most_a_bounded_number_of_times(make_ctx: MakeCtx) -> None:
    posts = [
        (
            str(n),
            PUBLISHED,
            f'<p>Gala el 24 de octubre <a href="https://t{n}.example.org/e">x</a></p>',
        )
        for n in range(20)
    ]
    serve_feed(*posts)
    for n in range(20):
        robots(f"t{n}.example.org")
        respx.get(f"https://t{n}.example.org/e").mock(
            return_value=httpx.Response(
                200, text="<html></html>", headers={"content-type": "text/html"}
            )
        )
    hosts = [f"t{n}.example.org" for n in range(20)]
    cfg = mastodon_cfg(
        params={"handle": "agenda@mastodon.example", "link_hosts": hosts, "year_policy": "infer"}
    )
    result = MastodonAdapter(cfg).fetch(make_ctx())
    pages = [c for c in respx.calls if c.request.url.path == "/e"]
    assert len(pages) == 8 and result.events == []


@respx.mock
def test_a_private_address_in_a_post_link_is_refused(make_ctx: MakeCtx) -> None:
    serve_feed(
        (
            "1",
            PUBLISHED,
            '<p>Gala en Adeje el 24 de octubre <a href="http://127.0.0.1/admin">x</a></p>',
        )
    )
    result = MastodonAdapter(mastodon_cfg()).fetch(make_ctx())
    assert [e.extra["derived_from"] for e in result.events] == ["post_text"]
    assert not any(c.request.url.host == "127.0.0.1" for c in respx.calls)


@respx.mock
def test_a_non_feed_response_is_invalid(make_ctx: MakeCtx) -> None:
    respx.get(FEED).mock(return_value=httpx.Response(200, content=b"<html>login</html>"))
    with pytest.raises(InvalidPayload):
        MastodonAdapter(mastodon_cfg()).fetch(make_ctx())


@respx.mock
def test_an_unreachable_account_raises_for_the_pipeline_to_record(make_ctx: MakeCtx) -> None:
    respx.get(FEED).mock(return_value=httpx.Response(404))
    with pytest.raises(HttpError):
        MastodonAdapter(mastodon_cfg()).fetch(make_ctx())


# --------------------------------------------------------------------------- bluesky


def bsky_post(
    rkey: str, text: str, *, handle: str = "agenda.bsky.social", links: tuple[str, ...] = (),
    external: str | None = None, reason: bool = False, created: str = "2026-10-14T10:00:00.000Z",
) -> dict[str, Any]:  # fmt: skip
    record: dict[str, Any] = {"$type": "app.bsky.feed.post", "text": text, "createdAt": created}
    if links:
        record["facets"] = [
            {"index": {"byteStart": 0, "byteEnd": 1},
             "features": [{"$type": "app.bsky.richtext.facet#link", "uri": uri}]}
            for uri in links
        ]  # fmt: skip
    post: dict[str, Any] = {
        "uri": f"at://did:plc:abc/app.bsky.feed.post/{rkey}",
        "author": {"did": "did:plc:abc", "handle": handle},
        "record": record,
    }
    if external:
        post["embed"] = {"$type": "app.bsky.embed.external#view", "external": {"uri": external}}
    item: dict[str, Any] = {"post": post}
    if reason:
        item["reason"] = {"$type": "app.bsky.feed.defs#reasonRepost"}
    return item


def serve_bsky(*items: dict[str, Any]) -> respx.Route:
    return respx.get(APPVIEW).mock(return_value=httpx.Response(200, json={"feed": list(items)}))


@respx.mock
def test_bluesky_asks_the_public_appview_for_the_author_feed(make_ctx: MakeCtx) -> None:
    route = serve_bsky()
    result = BlueskyAdapter(bluesky_cfg()).fetch(make_ctx())
    query = parse_qs(urlsplit(str(route.calls[0].request.url)).query)
    assert query["actor"] == ["agenda.bsky.social"]
    assert query["filter"] == ["posts_no_replies"]
    assert result.events == [] and result.pages == 1
    assert "authorization" not in {k.lower() for k in route.calls[0].request.headers}


@respx.mock
def test_bluesky_post_with_date_and_place_becomes_an_event(make_ctx: MakeCtx) -> None:
    serve_bsky(
        bsky_post("3kabc", "Taller infantil en Candelaria el domingo 25 de octubre a las 11:00")
    )
    result = BlueskyAdapter(bluesky_cfg()).fetch(make_ctx())
    [event] = result.events
    assert event.start_datetime == datetime(2026, 10, 25, 11, 0, tzinfo=TZ)
    assert event.municipality == "Candelaria" and event.island == "Tenerife"
    assert event.source_type == "bluesky"
    assert event.source_event_id == "at://did:plc:abc/app.bsky.feed.post/3kabc"
    assert event.extra["post_url"] == "https://bsky.app/profile/agenda.bsky.social/post/3kabc"
    assert event.extra["source_trust"] == "social"


@respx.mock
def test_bluesky_reposts_and_other_authors_are_ignored(make_ctx: MakeCtx) -> None:
    text = "Concierto en Adeje el 24 de octubre"
    serve_bsky(
        bsky_post("1", text, reason=True),
        bsky_post("2", text, handle="someone.else.social"),
        bsky_post("3", text),
    )
    result = BlueskyAdapter(bluesky_cfg()).fetch(make_ctx())
    assert [e.source_event_id for e in result.events] == ["at://did:plc:abc/app.bsky.feed.post/3"]
    assert result.records_received == 1


@respx.mock
def test_bluesky_link_card_and_facet_links_lead_to_event_pages(make_ctx: MakeCtx) -> None:
    robots("teatro.example.org")
    event_page("Gala de otoño", "2026-10-24T20:30:00+01:00")
    serve_bsky(bsky_post("1", "Gala el 24 de octubre", external=PAGE))
    [event] = BlueskyAdapter(bluesky_cfg()).fetch(make_ctx()).events
    assert event.title == "Gala de otoño" and event.official_event_url == PAGE

    respx.clear()
    robots("teatro.example.org")
    event_page("Gala de otoño", "2026-10-24T20:30:00+01:00")
    serve_bsky(bsky_post("2", "Gala el 24 de octubre", links=(PAGE,)))
    [event] = BlueskyAdapter(bluesky_cfg()).fetch(make_ctx()).events
    assert event.official_event_url == PAGE


@respx.mock
def test_bluesky_unknown_actor_is_an_error(make_ctx: MakeCtx) -> None:
    respx.get(APPVIEW).mock(
        return_value=httpx.Response(
            400, json={"error": "InvalidRequest", "message": "Profile not found"}
        )
    )
    with pytest.raises(HttpError):
        BlueskyAdapter(bluesky_cfg()).fetch(make_ctx())


@respx.mock
def test_bluesky_unexpected_payload_is_invalid(make_ctx: MakeCtx) -> None:
    respx.get(APPVIEW).mock(return_value=httpx.Response(200, json={"oops": 1}))
    with pytest.raises(InvalidPayload):
        BlueskyAdapter(bluesky_cfg()).fetch(make_ctx())


def test_a_handle_is_required() -> None:
    with pytest.raises(ValueError, match="handle"):
        BlueskyAdapter(bluesky_cfg(params={}))
    with pytest.raises(ValueError, match="handle"):
        MastodonAdapter(mastodon_cfg(params={}))


@respx.mock
def test_links_to_hosts_nobody_listed_are_never_fetched(make_ctx: MakeCtx) -> None:
    html = f'<p>Gala en Adeje el 24 de octubre <a href="{PAGE}">info</a></p>'
    serve_feed(("1", PUBLISHED, html))
    cfg = mastodon_cfg(params={"handle": "agenda@mastodon.example", "year_policy": "infer"})
    result = MastodonAdapter(cfg).fetch(make_ctx())
    assert not any(c.request.url.host == "teatro.example.org" for c in respx.calls)
    assert [e.extra["derived_from"] for e in result.events] == ["post_text"]


@respx.mock
def test_subdomains_of_a_listed_host_are_allowed(make_ctx: MakeCtx) -> None:
    link = "https://tickets.teatro.example.org/e/gala"
    serve_feed(("1", PUBLISHED, f'<p>Gala el 24 de octubre <a href="{link}">info</a></p>'))
    robots("tickets.teatro.example.org")
    event_page("Gala", "2026-10-24T20:30:00+01:00", url=link)
    [event] = MastodonAdapter(mastodon_cfg()).fetch(make_ctx()).events
    assert event.official_event_url == link


@respx.mock
def test_a_post_without_a_year_is_skipped_unless_inference_is_opted_in(
    make_ctx: MakeCtx,
) -> None:
    serve_feed(("1", PUBLISHED, "<p>Concierto en Adeje el 24 de octubre a las 21:00</p>"))
    strict = mastodon_cfg(params={"handle": "agenda@mastodon.example"})
    result = MastodonAdapter(strict).fetch(make_ctx())
    assert result.events == []
    assert [r for r, _ in result.discarded] == ["year not stated (year_policy: strict)"]

    stated = "<p>Concierto en Adeje el 24 de octubre de 2026 a las 21:00</p>"
    serve_feed(("2", PUBLISHED, stated))
    [event] = MastodonAdapter(strict).fetch(make_ctx()).events
    assert event.start_datetime == datetime(2026, 10, 24, 21, 0, tzinfo=TZ)
    assert event.extra["year_inferred"] is False


@respx.mock
def test_a_strict_post_without_a_year_does_not_adopt_the_year_of_a_linked_page(
    make_ctx: MakeCtx,
) -> None:
    serve_feed(("1", PUBLISHED, f'<p>Gala el 24 de octubre <a href="{PAGE}">info</a></p>'))
    robots("teatro.example.org")
    event_page("Gala de otoño", "2026-10-24T20:30:00+01:00")
    cfg = mastodon_cfg(
        params={"handle": "agenda@mastodon.example", "link_hosts": ["teatro.example.org"]}
    )
    result = MastodonAdapter(cfg).fetch(make_ctx())
    assert result.events == []
    assert [r for r, _ in result.discarded] == ["year not stated (year_policy: strict)"]


@respx.mock
def test_a_strict_post_with_a_stated_year_matches_the_linked_page(make_ctx: MakeCtx) -> None:
    html = f'<p>Gala el 24 de octubre de 2026 <a href="{PAGE}">info</a></p>'
    serve_feed(("1", PUBLISHED, html))
    robots("teatro.example.org")
    event_page("Gala de otoño", "2026-10-24T20:30:00+01:00")
    cfg = mastodon_cfg(
        params={"handle": "agenda@mastodon.example", "link_hosts": ["teatro.example.org"]}
    )
    [event] = MastodonAdapter(cfg).fetch(make_ctx()).events
    assert event.extra["derived_from"] == "event_page"
    assert event.start_datetime == datetime(2026, 10, 24, 20, 30, tzinfo=TZ)


@respx.mock
def test_the_same_day_and_month_in_another_year_is_not_a_match(make_ctx: MakeCtx) -> None:
    html = f'<p>Gala en Adeje el 24 de octubre de 2026 <a href="{PAGE}">info</a></p>'
    serve_feed(("1", PUBLISHED, html))
    robots("teatro.example.org")
    event_page("Gala de otoño", "2027-10-24T20:30:00+01:00")
    [event] = MastodonAdapter(mastodon_cfg()).fetch(make_ctx()).events
    assert event.extra["derived_from"] == "post_text"  # the page's 2027 date is not adopted
    assert event.start_datetime is None and event.start_date == date(2026, 10, 24)


@respx.mock
def test_an_inferred_year_still_matches_the_page_when_inference_is_opted_in(
    make_ctx: MakeCtx,
) -> None:
    serve_feed(("1", PUBLISHED, f'<p>Gala el 24 de octubre <a href="{PAGE}">info</a></p>'))
    robots("teatro.example.org")
    event_page("Gala de otoño", "2027-10-24T20:30:00+01:00")
    [event] = MastodonAdapter(mastodon_cfg()).fetch(make_ctx()).events  # year_policy: infer
    assert event.extra["derived_from"] == "event_page"


@respx.mock
def test_timestamps_without_an_offset_are_read_as_utc(make_ctx: MakeCtx) -> None:
    serve_feed(("1", "Wed, 14 Oct 2026 10:00:00 -0000", "<p>Fiesta en Adeje el 24 de octubre</p>"))
    assert len(MastodonAdapter(mastodon_cfg()).fetch(make_ctx()).events) == 1
    respx.clear()
    serve_bsky(bsky_post("1", "Fiesta en Adeje el 24 de octubre", created="2026-10-14T10:00:00"))
    assert len(BlueskyAdapter(bluesky_cfg()).fetch(make_ctx()).events) == 1


def test_params_reject_unknown_keys_and_policies() -> None:
    with pytest.raises(ValueError, match="year_policy"):
        MastodonAdapter(mastodon_cfg(params={"handle": "a@m.example", "year_policy": "guess"}))
    with pytest.raises(ValueError, match="surprise"):
        BlueskyAdapter(bluesky_cfg(params={"handle": "a.bsky.social", "surprise": 1}))
