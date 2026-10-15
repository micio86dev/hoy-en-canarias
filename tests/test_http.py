from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from canarias_events.http import (
    HttpError,
    InMemoryHttpCache,
    InvalidPayload,
    ResponseTooLarge,
    SafeHttpClient,
    TooManyRedirects,
    UnsafeUrlError,
    mask_secrets,
    same_host_as,
    validate_url,
)

PUBLIC_IP = "93.184.216.34"


def public_resolver(host: str) -> list[str]:
    return [PUBLIC_IP]


class Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def make_client(sleep: Recorder | None = None, **kwargs: Any) -> SafeHttpClient:
    kwargs.setdefault("resolver", public_resolver)
    kwargs.setdefault("min_interval", 0.0)
    return SafeHttpClient(sleep=sleep or Recorder(), **kwargs)


# --------------------------------------------------------------------------- URL validation


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com/",
        "http://127.0.0.1/",
        "http://127.1.2.3:8080/x",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data",
        "http://[fd00:ec2::254]/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://100.64.0.1/",
        "http://[::ffff:127.0.0.1]/",
        "http://user:pw@example.com/",
        "http://user@example.com/",
        "http:///nohost",
        "not a url",
        "",
    ],
)
def test_validate_url_rejects_unsafe(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_url(url, resolver=public_resolver)


def test_validate_url_rejects_hostname_resolving_to_private_ip() -> None:
    with pytest.raises(UnsafeUrlError):
        validate_url("https://evil.example/", resolver=lambda h: ["10.1.2.3"])


def test_validate_url_rejects_if_any_resolved_ip_is_private() -> None:
    with pytest.raises(UnsafeUrlError):
        validate_url("https://evil.example/", resolver=lambda h: [PUBLIC_IP, "127.0.0.1"])


def test_validate_url_rejects_unresolvable_host() -> None:
    def boom(host: str) -> list[str]:
        raise OSError("no such host")

    with pytest.raises(UnsafeUrlError):
        validate_url("https://nope.example/", resolver=boom)


def test_validate_url_accepts_public_http_and_https() -> None:
    assert validate_url("https://example.com/a?b=1", resolver=public_resolver)
    assert validate_url("http://example.com/", resolver=public_resolver)
    assert validate_url("http://93.184.216.34/", resolver=public_resolver)


def test_same_host_as() -> None:
    assert same_host_as("https://Example.com/a", "https://example.com/b?page=2")
    assert not same_host_as("https://example.com/a", "https://evil.com/b")
    assert not same_host_as("https://example.com/a", "not a url")


# --------------------------------------------------------------------------- masking


def test_mask_secrets_masks_sensitive_params() -> None:
    url = "https://api.test/x?apikey=SECRET1&q=hello&access_token=SECRET2&Key=SECRET3&page=2"
    masked = mask_secrets(url)
    assert "SECRET" not in masked
    assert "q=hello" in masked and "page=2" in masked
    assert "apikey=***" in masked


def test_mask_secrets_leaves_plain_url_alone() -> None:
    assert mask_secrets("https://a.test/p?x=1") == "https://a.test/p?x=1"


# --------------------------------------------------------------------------- fetching


@respx.mock
def test_get_returns_fetch_result_and_merges_params() -> None:
    route = respx.get("https://api.test/events").mock(
        return_value=httpx.Response(200, json={"ok": True}, headers={"X-A": "1"})
    )
    res = make_client().get("https://api.test/events", params={"page": 2})
    assert res.status == 200 and res.from_cache is False
    assert res.content == b'{"ok":true}' or b"ok" in res.content
    assert res.headers["x-a"] == "1"
    assert route.calls.last.request.url.params["page"] == "2"
    assert route.calls.last.request.headers["user-agent"].startswith("canarias-events-importer/0.1")


@respx.mock
def test_get_json() -> None:
    respx.get("https://api.test/j").mock(return_value=httpx.Response(200, json={"a": [1, 2]}))
    assert make_client().get_json("https://api.test/j") == {"a": [1, 2]}


@respx.mock
def test_get_json_invalid_payload() -> None:
    respx.get("https://api.test/j").mock(return_value=httpx.Response(200, content=b"<html>"))
    with pytest.raises(InvalidPayload):
        make_client().get_json("https://api.test/j")


@respx.mock
def test_client_error_raises_http_error_without_retry() -> None:
    route = respx.get("https://api.test/x").mock(return_value=httpx.Response(404))
    with pytest.raises(HttpError) as exc:
        make_client().get("https://api.test/x")
    assert exc.value.status == 404
    assert route.call_count == 1


@respx.mock
def test_unsafe_url_makes_no_request() -> None:
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(UnsafeUrlError):
        make_client().get("http://127.0.0.1/admin")
    assert route.call_count == 0


@respx.mock
def test_hostname_resolving_to_private_ip_blocked_at_client() -> None:
    route = respx.route().mock(return_value=httpx.Response(200))
    client = make_client(resolver=lambda h: ["192.168.0.10"])
    with pytest.raises(UnsafeUrlError):
        client.get("https://intranet.example/")
    assert route.call_count == 0


# --------------------------------------------------------------------------- redirects


@respx.mock
def test_redirect_followed_and_revalidated() -> None:
    respx.get("https://a.test/start").mock(
        return_value=httpx.Response(302, headers={"Location": "/final"})
    )
    respx.get("https://a.test/final").mock(return_value=httpx.Response(200, content=b"done"))
    res = make_client().get("https://a.test/start")
    assert res.content == b"done"
    assert res.url == "https://a.test/final"


@respx.mock
def test_custom_headers_dropped_on_https_to_http_downgrade() -> None:
    respx.get("https://a.test/start").mock(
        return_value=httpx.Response(302, headers={"Location": "http://a.test/final"})
    )
    final = respx.get("http://a.test/final").mock(return_value=httpx.Response(200, content=b"ok"))
    make_client().get("https://a.test/start", headers={"X-Api-Key": "secret"})
    assert "x-api-key" not in final.calls.last.request.headers


@respx.mock
def test_custom_headers_kept_on_same_host_same_scheme_redirect() -> None:
    respx.get("https://a.test/start").mock(
        return_value=httpx.Response(302, headers={"Location": "/final"})
    )
    final = respx.get("https://a.test/final").mock(return_value=httpx.Response(200, content=b"ok"))
    make_client().get("https://a.test/start", headers={"X-Api-Key": "secret"})
    assert final.calls.last.request.headers["x-api-key"] == "secret"


@respx.mock
def test_custom_headers_dropped_on_host_change() -> None:
    respx.get("https://a.test/start").mock(
        return_value=httpx.Response(302, headers={"Location": "https://b.test/final"})
    )
    final = respx.get("https://b.test/final").mock(return_value=httpx.Response(200, content=b"ok"))
    make_client().get("https://a.test/start", headers={"X-Api-Key": "secret"})
    assert "x-api-key" not in final.calls.last.request.headers


@respx.mock
def test_redirect_to_private_ip_blocked() -> None:
    respx.get("https://a.test/start").mock(
        return_value=httpx.Response(302, headers={"Location": "http://169.254.169.254/latest"})
    )
    private = respx.route(host="169.254.169.254").mock(return_value=httpx.Response(200))
    with pytest.raises(UnsafeUrlError):
        make_client().get("https://a.test/start")
    assert private.call_count == 0


@respx.mock
def test_redirect_to_hostname_resolving_private_blocked() -> None:
    respx.get("https://a.test/start").mock(
        return_value=httpx.Response(301, headers={"Location": "https://internal.test/"})
    )

    def resolver(host: str) -> list[str]:
        return ["10.0.0.1"] if host == "internal.test" else [PUBLIC_IP]

    with pytest.raises(UnsafeUrlError):
        make_client(resolver=resolver).get("https://a.test/start")


@respx.mock
def test_redirect_chain_limit() -> None:
    respx.get(url__regex=r"https://a\.test/r\d+").mock(
        side_effect=lambda req: httpx.Response(
            302, headers={"Location": f"/r{int(req.url.path[2:]) + 1}"}
        )
    )
    route_calls = respx.calls
    with pytest.raises(TooManyRedirects):
        make_client(max_redirects=5).get("https://a.test/r0")
    assert route_calls.call_count == 6  # initial request + 5 redirects, then abort


@respx.mock
def test_redirect_without_location_is_error() -> None:
    respx.get("https://a.test/x").mock(return_value=httpx.Response(302))
    with pytest.raises(HttpError):
        make_client().get("https://a.test/x")


@respx.mock
def test_custom_headers_not_forwarded_across_hosts() -> None:
    respx.get("https://a.test/x").mock(
        return_value=httpx.Response(302, headers={"Location": "https://b.test/y"})
    )
    second = respx.get("https://b.test/y").mock(return_value=httpx.Response(200))
    make_client().get("https://a.test/x", headers={"X-Api-Key": "SECRET"})
    assert "x-api-key" not in second.calls.last.request.headers


@respx.mock
def test_allowed_hosts_restriction() -> None:
    route = respx.route().mock(return_value=httpx.Response(200))
    client = make_client()
    with pytest.raises(UnsafeUrlError):
        client.get("https://evil.test/next", allowed_hosts=["good.test"])
    assert route.call_count == 0
    respx.get("https://good.test/next").mock(return_value=httpx.Response(200))
    assert client.get("https://good.test/next", allowed_hosts=["good.test"]).status == 200


@respx.mock
def test_allowed_hosts_applies_to_redirects() -> None:
    respx.get("https://good.test/a").mock(
        return_value=httpx.Response(302, headers={"Location": "https://other.test/b"})
    )
    with pytest.raises(UnsafeUrlError):
        make_client().get("https://good.test/a", allowed_hosts=["good.test"])


# --------------------------------------------------------------------------- size cap


@respx.mock
def test_size_cap_via_content_length() -> None:
    respx.get("https://a.test/big").mock(return_value=httpx.Response(200, content=b"x" * 2000))
    with pytest.raises(ResponseTooLarge):
        make_client(max_bytes=1000).get("https://a.test/big")


@respx.mock
def test_size_cap_while_streaming_without_content_length() -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(10):
            yield b"x" * 300

    respx.get("https://a.test/stream").mock(return_value=httpx.Response(200, content=chunks()))
    with pytest.raises(ResponseTooLarge):
        make_client(max_bytes=1000).get("https://a.test/stream")


@respx.mock
def test_body_at_limit_is_accepted() -> None:
    respx.get("https://a.test/ok").mock(return_value=httpx.Response(200, content=b"x" * 1000))
    assert len(make_client(max_bytes=1000).get("https://a.test/ok").content) == 1000


# --------------------------------------------------------------------------- retries


@respx.mock
def test_5xx_retried_then_succeeds_with_exponential_backoff() -> None:
    route = respx.get("https://a.test/x").mock(
        side_effect=[httpx.Response(503), httpx.Response(500), httpx.Response(200, content=b"ok")]
    )
    sleep = Recorder()
    res = make_client(sleep, backoff_base=1.0).get("https://a.test/x")
    assert res.content == b"ok"
    assert route.call_count == 3
    assert sleep.sleeps == [1.0, 2.0]


@respx.mock
def test_5xx_gives_up_after_retries() -> None:
    route = respx.get("https://a.test/x").mock(return_value=httpx.Response(502))
    with pytest.raises(HttpError) as exc:
        make_client(retries=3).get("https://a.test/x")
    assert exc.value.status == 502
    assert route.call_count == 4  # 1 attempt + 3 retries


@respx.mock
def test_timeout_errors_after_retries() -> None:
    route = respx.get("https://a.test/x").mock(side_effect=httpx.ConnectTimeout("slow"))
    sleep = Recorder()
    with pytest.raises(HttpError) as exc:
        make_client(sleep, retries=2).get("https://a.test/x")
    assert exc.value.status is None
    assert route.call_count == 3
    assert len(sleep.sleeps) == 2


@respx.mock
def test_transport_error_then_success() -> None:
    respx.get("https://a.test/x").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, content=b"ok")]
    )
    assert make_client().get("https://a.test/x").content == b"ok"


@respx.mock
def test_429_retry_after_seconds_respected() -> None:
    respx.get("https://a.test/x").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, content=b"ok"),
        ]
    )
    sleep = Recorder()
    assert make_client(sleep).get("https://a.test/x").content == b"ok"
    assert sleep.sleeps == [7.0]


@respx.mock
def test_429_retry_after_is_capped() -> None:
    respx.get("https://a.test/x").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "86400"}),
            httpx.Response(200, content=b"ok"),
        ]
    )
    sleep = Recorder()
    make_client(sleep, max_retry_after=60.0).get("https://a.test/x")
    assert sleep.sleeps == [60.0]


@respx.mock
def test_429_retry_after_http_date() -> None:
    respx.get("https://a.test/x").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "Thu, 15 Oct 2026 12:00:30 GMT"}),
            httpx.Response(200, content=b"ok"),
        ]
    )
    sleep = Recorder()
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
    make_client(sleep, wall_clock=lambda: now).get("https://a.test/x")
    assert sleep.sleeps == [30.0]


@respx.mock
def test_429_without_retry_after_uses_backoff() -> None:
    respx.get("https://a.test/x").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, content=b"ok")]
    )
    sleep = Recorder()
    make_client(sleep, backoff_base=0.5).get("https://a.test/x")
    assert sleep.sleeps == [0.5]


# --------------------------------------------------------------------------- rate limit


@respx.mock
def test_per_host_rate_limit() -> None:
    respx.get(host="a.test").mock(return_value=httpx.Response(200))
    respx.get(host="b.test").mock(return_value=httpx.Response(200))
    now = [100.0]
    sleep = Recorder()

    def fake_sleep(s: float) -> None:
        sleep(s)
        now[0] += s

    client = SafeHttpClient(
        resolver=public_resolver, sleep=fake_sleep, clock=lambda: now[0], min_interval=1.0
    )
    client.get("https://a.test/1")
    assert sleep.sleeps == []  # first request to a host is immediate
    client.get("https://b.test/1")
    assert sleep.sleeps == []  # other host is independent
    now[0] += 0.25
    client.get("https://a.test/2")
    assert sleep.sleeps == [pytest.approx(0.75)]


# --------------------------------------------------------------------------- conditional cache


@respx.mock
def test_etag_304_returns_cached_body() -> None:
    cache = InMemoryHttpCache()
    route = respx.get("https://a.test/c").mock(
        side_effect=[
            httpx.Response(200, content=b"v1", headers={"ETag": '"abc"'}),
            httpx.Response(304),
        ]
    )
    client = make_client(cache=cache)
    first = client.get("https://a.test/c")
    assert first.from_cache is False and first.content == b"v1"
    second = client.get("https://a.test/c")
    assert second.from_cache is True and second.content == b"v1" and second.status == 200
    assert route.calls[1].request.headers["if-none-match"] == '"abc"'


@respx.mock
def test_last_modified_sent_and_cache_updated() -> None:
    cache = InMemoryHttpCache()
    lm = "Wed, 14 Oct 2026 10:00:00 GMT"
    route = respx.get("https://a.test/c").mock(
        side_effect=[
            httpx.Response(200, content=b"v1", headers={"Last-Modified": lm}),
            httpx.Response(200, content=b"v2", headers={"ETag": "W/1"}),
        ]
    )
    client = make_client(cache=cache)
    client.get("https://a.test/c")
    second = client.get("https://a.test/c")
    assert route.calls[1].request.headers["if-modified-since"] == lm
    assert second.content == b"v2" and second.from_cache is False
    assert cache.get("https://a.test/c") == ("W/1", None, b"v2")


@respx.mock
def test_response_without_validators_not_cached() -> None:
    cache = InMemoryHttpCache()
    respx.get("https://a.test/c").mock(return_value=httpx.Response(200, content=b"v1"))
    make_client(cache=cache).get("https://a.test/c")
    assert cache.get("https://a.test/c") is None


@respx.mock
def test_304_without_cache_entry_is_error() -> None:
    respx.get("https://a.test/c").mock(return_value=httpx.Response(304))
    with pytest.raises(HttpError):
        make_client().get("https://a.test/c")


@respx.mock
def test_cache_key_includes_params() -> None:
    cache = InMemoryHttpCache()
    respx.get("https://a.test/c").mock(
        return_value=httpx.Response(200, content=b"v", headers={"ETag": "e"})
    )
    make_client(cache=cache).get("https://a.test/c", params={"page": 1})
    assert cache.get("https://a.test/c?page=1") is not None
    assert cache.get("https://a.test/c") is None


def test_default_settings() -> None:
    client = SafeHttpClient(resolver=public_resolver)
    assert client.timeout == 20.0
    assert client.max_bytes == 10 * 1024 * 1024
    assert client.max_redirects == 5
    assert client.retries == 3
    assert client.min_interval == 1.0
    client.close()


def test_logging_masks_secrets(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG", logger="canarias_events.http")
    with respx.mock:
        respx.get("https://a.test/x").mock(return_value=httpx.Response(200))
        make_client().get("https://a.test/x", params={"apikey": "TOPSECRET", "q": "1"})
    assert "TOPSECRET" not in caplog.text


def test_httpx_request_log_masks_secrets(caplog: pytest.LogCaptureFixture) -> None:
    # httpx itself logs the full request URL at INFO; the client must not leak query secrets.
    caplog.set_level("INFO")
    with respx.mock:
        respx.get("https://a.test/x").mock(return_value=httpx.Response(200))
        make_client().get("https://a.test/x", params={"apikey": "TOPSECRET", "q": "1"})
    assert "HTTP Request" in caplog.text
    assert "TOPSECRET" not in caplog.text


def test_host_interval_overrides_the_default_for_one_host() -> None:
    rec = Recorder()
    now = [0.0]

    def fake_sleep(seconds: float) -> None:
        rec.sleeps.append(seconds)
        now[0] += seconds

    client = make_client(sleep=fake_sleep, clock=lambda: now[0], min_interval=1.0)  # type: ignore[arg-type]
    client.set_host_interval("slow.example.org", 4.0)
    assert client.host_interval("slow.example.org") == 4.0
    assert client.host_interval("fast.example.org") == 1.0
    client.set_host_interval("slow.example.org", 2.0)  # never lowers an interval
    assert client.host_interval("slow.example.org") == 4.0
    with respx.mock:
        respx.get("https://slow.example.org/a").mock(return_value=httpx.Response(200, text="x"))
        client.get("https://slow.example.org/a")
        client.get("https://slow.example.org/a")
    assert rec.sleeps == [pytest.approx(4.0)]


def test_concurrent_requests_to_one_host_reserve_consecutive_slots() -> None:
    """Threads that arrive together must not all see the same 'last request' time."""
    waits: list[float] = []
    client = make_client(sleep=waits.append, clock=lambda: 100.0, min_interval=1.0)  # type: ignore[arg-type]
    for _ in range(3):  # a frozen clock is the worst case: everybody arrives at the same instant
        client._wait_for_host("https://same.example.org/x")
    client._wait_for_host("https://other.example.org/x")  # other hosts are not delayed
    assert waits == [pytest.approx(1.0), pytest.approx(2.0)]


def test_rate_limiting_is_thread_safe() -> None:
    from concurrent.futures import ThreadPoolExecutor

    slots: list[float] = []
    client = make_client(sleep=slots.append, clock=lambda: 50.0, min_interval=1.0)  # type: ignore[arg-type]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: client._wait_for_host("https://same.example.org/"), range(8)))
    assert sorted(slots) == [pytest.approx(float(n)) for n in range(1, 8)]
