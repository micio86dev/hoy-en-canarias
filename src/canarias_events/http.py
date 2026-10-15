"""SSRF-safe HTTP client: URL validation, manual redirects, size cap, retries, rate limit.

Every outbound request in the importer goes through :class:`SafeHttpClient`.

Known limitation: the hostname is resolved and validated here, then resolved again by the
HTTP stack at connect time (DNS rebinding window). The importer only talks to a fixed,
configured list of sources, which keeps that residual risk low.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import re
import socket
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import SplitResult, unquote, urljoin, urlsplit, urlunsplit

import httpx

logger = logging.getLogger("canarias_events.http")

DEFAULT_USER_AGENT = "canarias-events-importer/0.1 (+contact in README)"
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_EXTRA_BLOCKED = frozenset(
    {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")}
)
_SENSITIVE_PARAM = re.compile(r"(key|token|secret|passw|auth|signature)", re.IGNORECASE)


class FetchError(Exception):
    """Base class for all client errors."""


class UnsafeUrlError(FetchError):
    """The URL (or a redirect target) is not allowed."""


class ResponseTooLarge(FetchError):
    """The response body exceeded the configured size cap."""


class InvalidPayload(FetchError):
    """The response body could not be parsed as expected."""


class HttpError(FetchError):
    """Non-success HTTP status, or a transport failure when ``status`` is None."""

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status


class TooManyRedirects(HttpError):
    def __init__(self, message: str) -> None:
        super().__init__(None, message)


# --------------------------------------------------------------------------- helpers


def mask_secrets(url: str) -> str:
    """Mask userinfo and the value of query params that look like keys/tokens."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid url>"
    netloc = parts.netloc.rpartition("@")[2]
    pairs = []
    for pair in parts.query.split("&") if parts.query else []:
        name, sep, _value = pair.partition("=")
        if sep and _SENSITIVE_PARAM.search(unquote(name)):
            pair = f"{name}=***"
        pairs.append(pair)
    return urlunsplit(parts._replace(netloc=netloc, query="&".join(pairs)))


class _MaskUrlSecrets(logging.Filter):
    """httpx logs every request URL at INFO; mask key/token query values in those records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                mask_secrets(str(arg)) if isinstance(arg, httpx.URL) else arg for arg in record.args
            )
        return True


logging.getLogger("httpx").addFilter(_MaskUrlSecrets())


def _default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip in _EXTRA_BLOCKED
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def validate_url(url: str, resolver: Callable[[str], list[str]] | None = None) -> SplitResult:
    """Validate scheme, host, userinfo and every resolved IP. Raises UnsafeUrlError."""
    shown = mask_secrets(url)
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError as exc:
        raise UnsafeUrlError(f"invalid URL: {shown}") from exc
    if parts.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"scheme not allowed: {shown}")
    if "@" in parts.netloc:
        raise UnsafeUrlError("credentials in URL are not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError(f"URL has no host: {shown}")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            addresses = (resolver or _default_resolver)(host)
        except OSError as exc:
            raise UnsafeUrlError(f"cannot resolve host: {host}") from exc
    if not addresses:
        raise UnsafeUrlError(f"host did not resolve: {host}")
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError as exc:
            raise UnsafeUrlError(f"bad resolved address for {host}") from exc
        if _is_blocked(ip):
            raise UnsafeUrlError(f"host {host} resolves to a blocked address")
    return parts


def same_host_as(base_url: str, candidate_url: str) -> bool:
    """True when both URLs have the same (case-insensitive) hostname."""
    try:
        a = urlsplit(base_url).hostname
        b = urlsplit(candidate_url).hostname
    except ValueError:
        return False
    return bool(a) and bool(b) and a == b  # urlsplit lowercases hostname


def _keeps_credentials(origin_url: str, target_url: str) -> bool:
    """Custom headers may follow a redirect only on the same host without an https downgrade."""
    if not same_host_as(origin_url, target_url):
        return False
    return not (
        urlsplit(origin_url).scheme.lower() == "https"
        and urlsplit(target_url).scheme.lower() != "https"
    )


def _parse_retry_after(value: str, now: datetime) -> float | None:
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - now).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


# --------------------------------------------------------------------------- cache

CacheEntry = tuple[str | None, str | None, bytes]


class HttpCache(Protocol):
    def get(self, url: str) -> CacheEntry | None: ...

    def set(self, url: str, etag: str | None, last_modified: str | None, body: bytes) -> None: ...


class InMemoryHttpCache:
    def __init__(self) -> None:
        self._data: dict[str, CacheEntry] = {}

    def get(self, url: str) -> CacheEntry | None:
        return self._data.get(url)

    def set(self, url: str, etag: str | None, last_modified: str | None, body: bytes) -> None:
        self._data[url] = (etag, last_modified, body)


# --------------------------------------------------------------------------- client


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    from_cache: bool = False


class SafeHttpClient:
    def __init__(
        self,
        *,
        timeout: float = 20.0,
        total_timeout: float = 60.0,
        max_bytes: int = 10 * 1024 * 1024,
        max_redirects: int = 5,
        retries: int = 3,
        backoff_base: float = 0.5,
        max_retry_after: float = 60.0,
        min_interval: float = 1.0,
        user_agent: str = DEFAULT_USER_AGENT,
        resolver: Callable[[str], list[str]] | None = None,
        cache: HttpCache | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout = timeout
        self.total_timeout = total_timeout
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.retries = retries
        self.backoff_base = backoff_base
        self.max_retry_after = max_retry_after
        self.min_interval = min_interval
        self.user_agent = user_agent
        self._resolver = resolver
        self._cache = cache
        self._sleep = sleep
        self._clock = clock
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._last_request: dict[str, float] = {}
        # trust_env=False: never route through env proxies, which would bypass IP validation.
        self._client = client or httpx.Client(
            follow_redirects=False, timeout=timeout, trust_env=False
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SafeHttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ public API

    def get(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        allowed_hosts: Collection[str] | None = None,
    ) -> FetchResult:
        allowed = {h.lower() for h in allowed_hosts} if allowed_hosts is not None else None
        self._check_url(url, allowed)
        full = str(httpx.URL(url).copy_merge_params(params)) if params else url
        cached = self._cache.get(full) if self._cache else None
        current = full
        hop = 0
        while True:
            self._check_url(current, allowed)
            send = {"User-Agent": self.user_agent, "Accept": "*/*"}
            if headers and _keeps_credentials(full, current):
                send.update(headers)
            if hop == 0 and cached is not None:
                etag, last_modified, _ = cached
                if etag:
                    send["If-None-Match"] = etag
                if last_modified:
                    send["If-Modified-Since"] = last_modified
            status, resp_headers, body = self._fetch_with_retries(current, send)
            if status in REDIRECT_STATUSES:
                location = resp_headers.get("location")
                if not location:
                    raise HttpError(status, f"redirect without Location: {mask_secrets(current)}")
                if hop >= self.max_redirects:
                    raise TooManyRedirects(
                        f"more than {self.max_redirects} redirects: {mask_secrets(full)}"
                    )
                hop += 1
                current = urljoin(current, location)
                continue
            if status == 304 and hop == 0 and cached is not None:
                return FetchResult(current, 200, resp_headers, cached[2], from_cache=True)
            if not 200 <= status < 300:
                raise HttpError(status, f"HTTP {status} for {mask_secrets(current)}")
            if hop == 0 and self._cache is not None:
                etag = resp_headers.get("etag")
                last_modified = resp_headers.get("last-modified")
                if etag or last_modified:
                    self._cache.set(full, etag, last_modified, body)
            return FetchResult(current, status, resp_headers, body)

    def get_json(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        allowed_hosts: Collection[str] | None = None,
    ) -> Any:
        result = self.get(url, params=params, headers=headers, allowed_hosts=allowed_hosts)
        try:
            return json.loads(result.content)
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidPayload(f"invalid JSON from {mask_secrets(result.url)}") from exc

    # ------------------------------------------------------------------ internals

    def _check_url(self, url: str, allowed: set[str] | None) -> None:
        parts = validate_url(url, self._resolver)
        if allowed is not None and (parts.hostname or "") not in allowed:
            raise UnsafeUrlError(f"host not allowed: {parts.hostname}")

    def _backoff(self, attempt: int) -> float:
        return float(self.backoff_base * (2**attempt))

    def _wait_for_host(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        last = self._last_request.get(host)
        if last is not None:
            wait = self.min_interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    def _fetch_with_retries(
        self, url: str, headers: dict[str, str]
    ) -> tuple[int, dict[str, str], bytes]:
        attempt = 0
        while True:
            try:
                status, resp_headers, body = self._once(url, headers)
            except httpx.TransportError as exc:
                if attempt >= self.retries:
                    raise HttpError(
                        None, f"request failed ({type(exc).__name__}): {mask_secrets(url)}"
                    ) from exc
                self._sleep(self._backoff(attempt))
                attempt += 1
                continue
            if status == 429 or status >= 500:
                if attempt >= self.retries:
                    raise HttpError(status, f"HTTP {status} for {mask_secrets(url)}")
                delay = self._backoff(attempt)
                raw = resp_headers.get("retry-after")
                if raw is not None:
                    parsed = _parse_retry_after(raw, self._wall_clock())
                    if parsed is not None:
                        delay = min(parsed, self.max_retry_after)
                self._sleep(delay)
                attempt += 1
                continue
            return status, resp_headers, body

    def _once(self, url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        self._wait_for_host(url)
        logger.debug("GET %s", mask_secrets(url))
        deadline = self._clock() + self.total_timeout
        with self._client.stream("GET", url, headers=headers) as response:
            resp_headers = {k.lower(): v for k, v in response.headers.items()}
            status = response.status_code
            if not 200 <= status < 300:
                return status, resp_headers, b""  # body of redirects/errors is not needed
            declared = resp_headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
                raise ResponseTooLarge(f"declared size {declared} exceeds {self.max_bytes}")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > self.max_bytes:
                    raise ResponseTooLarge(f"response exceeds {self.max_bytes} bytes")
                if self._clock() > deadline:
                    raise HttpError(None, f"total timeout exceeded: {mask_secrets(url)}")
                chunks.append(chunk)
            return status, resp_headers, b"".join(chunks)
