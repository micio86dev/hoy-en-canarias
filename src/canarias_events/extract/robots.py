"""robots.txt compliance for HTML acquisition (RFC 9309 and Google conventions).

* 404 and other 4xx (except 429): no restrictions. A protected page still answers 401/403 on
  its own fetch, which the adapter reports as blocked (never bypassed).
* 5xx, 429, timeouts and transport errors: the host is disallowed for the rest of the run.
* The group of our own user-agent token wins over ``*``; the longest matching rule wins and
  ``Allow`` wins a tie; ``*`` and ``$`` wildcards work.
* ``Crawl-delay`` raises the per-host request interval of the HTTP client; a delay above
  ``max_crawl_delay`` means the host is skipped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from canarias_events.http import FetchError, HttpError, SafeHttpClient

USER_AGENT_TOKEN = "canarias-events-importer"
MAX_ROBOTS_BYTES = 512 * 1024
DEFAULT_MAX_CRAWL_DELAY = 30.0

logger = logging.getLogger("canarias_events.robots")


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)
    delay: float | None = None


@dataclass
class _Rules:
    groups: list[_Group] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)

    def select(self, token: str) -> list[_Group]:
        wanted = token.lower()
        own = [g for g in self.groups if any(a.split("/")[0].strip() == wanted for a in g.agents)]
        return own or [g for g in self.groups if "*" in g.agents]


def parse_robots(text: str) -> _Rules:
    """Tolerant robots.txt parser (comments, BOM, odd casing, stray lines are ignored)."""
    rules = _Rules()
    current: _Group | None = None
    collecting_agents = False
    for raw in text.lstrip("﻿").replace("\x00", "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        field_name, _, value = line.partition(":")
        name, value = field_name.strip().lower(), value.strip()
        if name == "user-agent":
            if current is None or not collecting_agents:
                current = _Group()
                rules.groups.append(current)
            if value:  # an empty User-agent line names nobody
                current.agents.append(value.lower())
            collecting_agents = True
            continue
        if name == "sitemap":
            if value:
                rules.sitemaps.append(value)
            continue
        collecting_agents = False
        if current is None:
            continue
        if name in ("allow", "disallow"):
            if value:  # an empty Disallow allows everything
                current.rules.append((name == "allow", value))
        elif name == "crawl-delay":
            try:
                delay = float(value)
            except ValueError:
                continue
            if delay >= 0 and current.delay is None:
                current.delay = delay
    return rules


def _matches(pattern: str, target: str) -> bool:
    """Prefix match with ``*`` wildcards and an optional ``$`` end anchor (linear backtracking)."""
    pattern = unquote(pattern[:512])
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    p = t = 0
    star = -1
    mark = 0
    while t < len(target):
        if p < len(pattern) and pattern[p] == "*":
            star, mark = p, t
            p += 1
        elif p < len(pattern) and pattern[p] == target[t]:
            p += 1
            t += 1
        elif p == len(pattern) and not anchored:
            return True  # the whole pattern matched a prefix of the path
        elif star >= 0:
            mark += 1
            p, t = star + 1, mark
        else:
            return False
    while p < len(pattern) and pattern[p] == "*":
        p += 1
    return p == len(pattern)


def _path_allowed(groups: list[_Group], path_and_query: str) -> bool:
    target = unquote(path_and_query)
    best_length = -1
    best_allow = True
    for group in groups:
        for allow, pattern in group.rules:
            if not _matches(pattern, target):
                continue
            length = len(unquote(pattern))
            if length > best_length or (length == best_length and allow):
                best_length, best_allow = length, allow
    return best_allow


@dataclass
class _Entry:
    state: str  # rules | no-robots | unavailable | crawl-delay-too-long
    rules: _Rules | None = None
    groups: list[_Group] = field(default_factory=list)
    delay: float | None = None


class RobotsPolicy:
    def __init__(
        self,
        client: SafeHttpClient,
        *,
        user_agent_token: str = USER_AGENT_TOKEN,
        max_crawl_delay: float = DEFAULT_MAX_CRAWL_DELAY,
    ) -> None:
        self._client = client
        self._token = user_agent_token
        self._max_delay = max_crawl_delay
        self._cache: dict[tuple[str, str, int | None], _Entry] = {}

    # ------------------------------------------------------------------ public API

    def allowed(self, url: str) -> bool:
        origin = self._origin(url)
        if origin is None:
            return False
        entry = self._entry(origin)
        if entry.state in ("unavailable", "crawl-delay-too-long"):
            return False
        if entry.rules is None:
            return True
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        return _path_allowed(entry.groups, target)

    def status(self, url: str) -> str:
        origin = self._origin(url)
        return "invalid-url" if origin is None else self._entry(origin).state

    def crawl_delay(self, url: str) -> float | None:
        origin = self._origin(url)
        return None if origin is None else self._entry(origin).delay

    def sitemaps(self, url: str) -> list[str]:
        origin = self._origin(url)
        if origin is None:
            return []
        rules = self._entry(origin).rules
        return list(rules.sitemaps) if rules is not None else []

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int | None] | None:
        try:
            parts = urlsplit(url.strip())
            host = parts.hostname
            port = parts.port
        except ValueError:
            return None
        if parts.scheme.lower() not in ("http", "https") or not host:
            return None
        default = 443 if parts.scheme.lower() == "https" else 80
        return parts.scheme.lower(), host, None if port == default else port

    def _entry(self, origin: tuple[str, str, int | None]) -> _Entry:
        cached = self._cache.get(origin)
        if cached is None:
            cached = self._fetch(origin)
            self._cache[origin] = cached
            if cached.state == "rules" and cached.delay:
                self._client.set_host_interval(origin[1], cached.delay)
        return cached

    def _fetch(self, origin: tuple[str, str, int | None]) -> _Entry:
        scheme, host, port = origin
        netloc = f"[{host}]" if ":" in host else host
        if port:
            netloc = f"{netloc}:{port}"
        url = f"{scheme}://{netloc}/robots.txt"
        # robots.txt may redirect between the bare and the www host of the same site.
        bare = host.removeprefix("www.")
        try:
            result = self._client.get(url, allowed_hosts={host, bare, f"www.{bare}"})
        except HttpError as exc:
            if exc.status is not None and 400 <= exc.status < 500 and exc.status != 429:
                return _Entry("no-robots")
            logger.warning("robots.txt unavailable for %s (%s): host skipped this run", host, exc)
            return _Entry("unavailable")
        except FetchError as exc:
            logger.warning("robots.txt unavailable for %s (%s): host skipped this run", host, exc)
            return _Entry("unavailable")
        rules = parse_robots(result.content[:MAX_ROBOTS_BYTES].decode("utf-8", errors="replace"))
        groups = rules.select(self._token)
        delays = [g.delay for g in groups if g.delay is not None]
        delay = delays[0] if delays else None
        if delay is not None and delay > self._max_delay:
            return _Entry("crawl-delay-too-long", rules, groups, delay)
        return _Entry("rules", rules, groups, delay)
