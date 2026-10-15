"""robots.txt compliance: caching, failure conventions, crawl-delay."""

from __future__ import annotations

import httpx
import pytest
import respx

from canarias_events.extract.robots import USER_AGENT_TOKEN, RobotsPolicy
from canarias_events.http import SafeHttpClient
from tests.conftest import FIXTURES, PUBLIC_IP


def _robots(name: str) -> str:
    return (FIXTURES / "robots" / f"{name}.txt").read_text(encoding="utf-8")


def _client(**kwargs: object) -> SafeHttpClient:
    kwargs.setdefault("resolver", lambda host: [PUBLIC_IP])
    kwargs.setdefault("min_interval", 0.0)
    kwargs.setdefault("retries", 0)
    kwargs.setdefault("sleep", lambda _s: None)
    return SafeHttpClient(**kwargs)  # type: ignore[arg-type]


def test_token_identifies_the_importer() -> None:
    assert USER_AGENT_TOKEN == "canarias-events-importer"


@respx.mock
def test_rules_are_applied_and_robots_is_fetched_once_per_host() -> None:
    route = respx.get("https://www.webtenerife.com/robots.txt").mock(
        return_value=httpx.Response(200, text=_robots("webtenerife"))
    )
    policy = RobotsPolicy(_client())
    assert policy.allowed("https://www.webtenerife.com/agenda/2026/10/eolica-festival-iter/")
    assert not policy.allowed("https://www.webtenerife.com/sitecore/service/x")
    assert not policy.allowed("https://www.webtenerife.com/upload/a.pdf")
    assert policy.allowed("https://www.webtenerife.com/sitemap.xml/")
    assert route.call_count == 1


@respx.mock
def test_sitemaps_declared_in_robots_are_exposed() -> None:
    respx.get("https://www.webtenerife.com/robots.txt").mock(
        return_value=httpx.Response(200, text=_robots("webtenerife"))
    )
    policy = RobotsPolicy(_client())
    assert policy.sitemaps("https://www.webtenerife.com/agenda/") == [
        "https://www.webtenerife.com/sitemap.xml/"
    ]


@respx.mock
def test_missing_robots_allows_everything() -> None:
    respx.get("https://www.teatroleal.es/robots.txt").mock(return_value=httpx.Response(404))
    policy = RobotsPolicy(_client())
    assert policy.allowed("https://www.teatroleal.es/programacion/")
    assert policy.status("https://www.teatroleal.es/") == "no-robots"


@respx.mock
def test_html_served_instead_of_robots_allows_everything() -> None:
    respx.get("https://www.teatroleal.es/robots.txt").mock(
        return_value=httpx.Response(200, text=_robots("teatroleal"))
    )
    assert RobotsPolicy(_client()).allowed("https://www.teatroleal.es/programacion/")


@respx.mock
def test_empty_disallow_allows_everything() -> None:
    respx.get("https://www.adeje.es/robots.txt").mock(
        return_value=httpx.Response(200, text=_robots("adeje"))
    )
    assert RobotsPolicy(_client()).allowed("https://www.adeje.es/agenda")


@respx.mock
@pytest.mark.parametrize("status", [500, 502, 503])
def test_server_errors_disallow_for_this_run_without_retrying(status: int) -> None:
    route = respx.get("https://down.example.org/robots.txt").mock(
        return_value=httpx.Response(status)
    )
    policy = RobotsPolicy(_client())
    assert not policy.allowed("https://down.example.org/agenda")
    assert not policy.allowed("https://down.example.org/other")
    assert route.call_count == 1
    assert policy.status("https://down.example.org/") == "unavailable"


@respx.mock
def test_timeout_disallows_for_this_run() -> None:
    respx.get("https://slow.example.org/robots.txt").mock(side_effect=httpx.ReadTimeout("slow"))
    assert not RobotsPolicy(_client()).allowed("https://slow.example.org/agenda")


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
def test_client_errors_other_than_429_mean_no_restrictions(status: int) -> None:
    respx.get("https://closed.example.org/robots.txt").mock(return_value=httpx.Response(status))
    assert RobotsPolicy(_client()).allowed("https://closed.example.org/agenda")


@respx.mock
def test_too_many_requests_is_treated_as_unavailable() -> None:
    respx.get("https://busy.example.org/robots.txt").mock(return_value=httpx.Response(429))
    assert not RobotsPolicy(_client()).allowed("https://busy.example.org/agenda")


@respx.mock
def test_our_own_group_takes_precedence_over_the_wildcard_group() -> None:
    body = (
        "User-agent: *\nDisallow:\n\n"
        f"User-agent: {USER_AGENT_TOKEN}\nDisallow: /private/\nCrawl-delay: 7\n"
    )
    respx.get("https://own.example.org/robots.txt").mock(
        return_value=httpx.Response(200, text=body)
    )
    policy = RobotsPolicy(_client())
    assert not policy.allowed("https://own.example.org/private/x")
    assert policy.allowed("https://own.example.org/public/x")
    assert policy.crawl_delay("https://own.example.org/") == 7.0


@respx.mock
def test_blanket_disallow_for_everyone() -> None:
    respx.get("https://nope.example.org/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    )
    assert not RobotsPolicy(_client()).allowed("https://nope.example.org/agenda")


@respx.mock
def test_crawl_delay_raises_the_per_host_interval() -> None:
    respx.get("https://www.arona.org/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nCrawl-delay: 5\nDisallow: /admin/\n")
    )
    slept: list[float] = []
    now = [100.0]

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    client = _client(min_interval=1.0, sleep=fake_sleep, clock=lambda: now[0])
    policy = RobotsPolicy(client)
    assert policy.crawl_delay("https://www.arona.org/Agenda") == 5.0
    assert client.host_interval("www.arona.org") == 5.0
    assert client.host_interval("other.example.org") == 1.0
    respx.get("https://www.arona.org/Agenda").mock(return_value=httpx.Response(200, text="ok"))
    client.get("https://www.arona.org/Agenda")
    client.get("https://www.arona.org/Agenda")
    assert slept and max(slept) == pytest.approx(5.0)


@respx.mock
def test_other_bots_crawl_delay_does_not_apply_to_us() -> None:
    respx.get("https://www.arona.org/robots.txt").mock(
        return_value=httpx.Response(200, text=_robots("arona"))
    )
    policy = RobotsPolicy(_client())
    assert policy.crawl_delay("https://www.arona.org/Agenda") is None  # delay is msnbot's
    assert policy.allowed("https://www.arona.org/Agenda/ctl/Ver/mid/429?id=91690")
    assert not policy.allowed("https://www.arona.org/admin/x")


@respx.mock
def test_crawl_delay_beyond_the_limit_means_skip_the_host() -> None:
    respx.get("https://tarpit.example.org/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nCrawl-delay: 300\n")
    )
    policy = RobotsPolicy(_client(), max_crawl_delay=30.0)
    assert not policy.allowed("https://tarpit.example.org/agenda")
    assert policy.status("https://tarpit.example.org/") == "crawl-delay-too-long"


@respx.mock
def test_hosts_and_schemes_are_cached_separately() -> None:
    a = respx.get("https://a.example.org/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /x\n")
    )
    b = respx.get("https://b.example.org/robots.txt").mock(return_value=httpx.Response(404))
    policy = RobotsPolicy(_client())
    assert not policy.allowed("https://a.example.org/x")
    assert policy.allowed("https://b.example.org/x")
    assert a.call_count == b.call_count == 1


def test_non_http_urls_are_never_allowed() -> None:
    policy = RobotsPolicy(_client())
    for url in (
        "javascript:alert(1)",
        "ftp://x.example.org/a",
        "file:///etc/passwd",
        "not a url",
        "",
    ):
        assert not policy.allowed(url)


@respx.mock
def test_binary_garbage_in_robots_does_not_crash() -> None:
    respx.get("https://junk.example.org/robots.txt").mock(
        return_value=httpx.Response(200, content=b"\xff\xfe\x00User-agent: *\n\x80Disallow: /a\n")
    )
    assert RobotsPolicy(_client()).allowed("https://junk.example.org/b")


@respx.mock
def test_private_hosts_are_still_blocked_by_the_ssrf_guard() -> None:
    policy = RobotsPolicy(_client(resolver=lambda host: ["127.0.0.1"]))
    assert not policy.allowed("http://localhost/agenda")
    assert policy.status("http://localhost/") == "unavailable"


@respx.mock
def test_longest_match_wins_and_allow_wins_ties() -> None:
    body = (
        "User-agent: *\nAllow: /\nDisallow: /private\nAllow: /private/open\n"
        "Disallow: /tie\nAllow: /tie\n"
    )
    respx.get("https://rfc.example.org/robots.txt").mock(
        return_value=httpx.Response(200, text=body)
    )
    policy = RobotsPolicy(_client())
    assert not policy.allowed("https://rfc.example.org/private/a")
    assert policy.allowed("https://rfc.example.org/private/open/a")
    assert policy.allowed("https://rfc.example.org/tie")
    assert policy.allowed("https://rfc.example.org/public")


@respx.mock
def test_wildcards_and_end_anchor_are_honoured() -> None:
    body = "User-agent: *\nDisallow: /*/private\nDisallow: /*.pdf$\nDisallow: /search?*q=\n"
    respx.get("https://wild.example.org/robots.txt").mock(
        return_value=httpx.Response(200, text=body)
    )
    policy = RobotsPolicy(_client())
    assert not policy.allowed("https://wild.example.org/a/private")
    assert not policy.allowed("https://wild.example.org/docs/a.pdf")
    assert policy.allowed("https://wild.example.org/docs/a.pdf.html")
    assert not policy.allowed("https://wild.example.org/search?page=2&q=x")
    assert policy.allowed("https://wild.example.org/search?page=2")


@respx.mock
def test_paths_are_case_sensitive_and_percent_decoded() -> None:
    body = "User-agent: *\nDisallow: /Admin/\nDisallow: /a b/\n"
    respx.get("https://case.example.org/robots.txt").mock(
        return_value=httpx.Response(200, text=body)
    )
    policy = RobotsPolicy(_client())
    assert not policy.allowed("https://case.example.org/Admin/x")
    assert policy.allowed("https://case.example.org/admin/x")
    assert not policy.allowed("https://case.example.org/a%20b/x")


@respx.mock
def test_robots_redirecting_between_bare_and_www_host_is_followed() -> None:
    respx.get("https://example.org/robots.txt").mock(
        return_value=httpx.Response(301, headers={"location": "https://www.example.org/robots.txt"})
    )
    respx.get("https://www.example.org/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /x\n")
    )
    assert not RobotsPolicy(_client()).allowed("https://example.org/x")


@respx.mock
def test_hostile_wildcard_patterns_do_not_hang() -> None:
    import time

    body = "User-agent: *\nDisallow: /" + "*a" * 200 + "b\n"
    respx.get("https://evil.example.org/robots.txt").mock(
        return_value=httpx.Response(200, text=body)
    )
    policy = RobotsPolicy(_client())
    started = time.monotonic()
    assert policy.allowed("https://evil.example.org/" + "a" * 3000)
    assert time.monotonic() - started < 2


@respx.mock
@pytest.mark.parametrize(
    "agent", ["", "events", "canarias", "import", "canarias-events-importer-x"]
)
def test_only_the_exact_product_token_selects_our_group(agent: str) -> None:
    body = f"User-agent: {agent}\nAllow: /\n\nUser-agent: *\nDisallow: /\n"
    respx.get("https://tok.example.org/robots.txt").mock(
        return_value=httpx.Response(200, text=body)
    )
    assert not RobotsPolicy(_client()).allowed("https://tok.example.org/x")


@respx.mock
def test_token_with_version_and_odd_case_matches() -> None:
    body = "User-agent: Canarias-Events-Importer/0.1\nDisallow: /x\n\nUser-agent: *\nDisallow:\n"
    respx.get("https://tok2.example.org/robots.txt").mock(
        return_value=httpx.Response(200, text=body)
    )
    assert not RobotsPolicy(_client()).allowed("https://tok2.example.org/x")


@respx.mock
def test_explicit_default_port_shares_the_cache_entry() -> None:
    route = respx.get("https://port.example.org/robots.txt").mock(return_value=httpx.Response(404))
    policy = RobotsPolicy(_client())
    assert policy.allowed("https://port.example.org/a")
    assert policy.allowed("https://port.example.org:443/b")
    assert route.call_count == 1
