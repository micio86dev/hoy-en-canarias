"""``validate-source``: is this URL (or configured source) worth turning into a source?

Checks reachability, content type, robots.txt, whether it yields events, how complete they
are, how many fall in the coming weeks and, if there is history, how reliable the source
has been. It scores the result and suggests an acquisition mode; a person decides. Every
request goes through ``SafeHttpClient`` and nothing is stored here.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from canarias_events.date_range import TZ, explicit_window, next_week
from canarias_events.extract.guards import is_challenge_page
from canarias_events.extract.robots import RobotsPolicy
from canarias_events.extract.util import parse_html
from canarias_events.health import summarize
from canarias_events.http import FetchError, HttpError, SafeHttpClient, mask_secrets
from canarias_events.models import Event
from canarias_events.pipeline import event_overlaps
from canarias_events.social import NOT_ACQUIRABLE, classify_social_url
from canarias_events.sources.base import FetchContext, SourceConfig, SourceFetchResult
from canarias_events.sources.ical import IcalAdapter
from canarias_events.sources.registry import create_adapter
from canarias_events.sources.rss import RssAdapter
from canarias_events.sources.web import WebAdapter
from canarias_events.statuses import BLOCKED, RATE_LIMITED, ROBOTS_DISALLOWED

logger = logging.getLogger(__name__)
COVERAGE_DAYS = 30
MIN_QUALITY = 0.6  # share of events with title, date and place for a "verified" suggestion
_RE_FEED = re.compile(rb"<(?:rss|feed)[\s>]", re.IGNORECASE)
# Acquisition mode (the social registry's vocabulary) each content kind or adapter suggests.
_KIND_MODES = {"html": "html", "web": "html", "ical": "ical", "feed": "rss", "rss": "rss"}
_FORMAT_KINDS = frozenset({"ical", "feed", "rss"})  # a feed is worth a mode even when empty
TRANSIENT_HTTP = 429  # plus every 5xx and transport failures (no status)
GONE_HTTP = frozenset({404, 410})  # a definite answer: the page is not there any more


@dataclass
class ValidationReport:
    url: str
    source_id: str | None = None
    reachable: bool = False
    http_status: int | None = None
    content_type: str | None = None
    kind: str = "unknown"  # html | ical | feed | json | other | social | unknown
    robots: str = "n/a"  # allowed | disallowed | unavailable | n/a
    blocked: str | None = None  # why the access is denied (never bypassed)
    events_found: int = 0
    strategy: str | None = None
    quality: float | None = None  # share of events with title + start date + place
    in_next_30_days: int = 0
    in_target_week: int = 0
    reliability: dict[str, Any] | None = None
    terms: str = "unknown"
    score: int = 0
    suggested_mode: str | None = None
    suggested_state: str = "candidate"
    # Set when the check could not reach a verdict (outage, parse error): saving it must not
    # overwrite what was known. ``transient`` marks the ones worth retrying later.
    error: str | None = None
    transient: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _complete(event: Event) -> bool:
    has_start = event.start_datetime is not None or event.start_date is not None
    return (
        bool(event.title)
        and has_start
        and bool(event.venue_name or event.municipality or event.address)
    )


def _terms(html: bytes) -> str | None:
    root = parse_html(html)
    if root is None:
        return None
    for node in root.xpath('//link[@rel="license"][@href] | //a[@rel="license"][@href]'):
        return f"declared: {node.get('href').strip()}"
    return None


def _kind(content_type: str, body: bytes) -> str:
    ctype, head = content_type.lower(), body.lstrip()[:512]
    if "text/calendar" in ctype or head.startswith(b"BEGIN:VCALENDAR"):
        return "ical"
    if "json" in ctype and "ld+json" not in ctype:
        return "json"
    if (
        "rss" in ctype
        or "atom" in ctype
        or (("xml" in ctype or head.startswith(b"<?xml")) and _RE_FEED.search(body[:4096]))
    ):
        return "feed"
    if "html" in ctype or head[:15].lower().startswith((b"<!doctype html", b"<html")):
        return "html"
    return "other"


def _social(report: ValidationReport, platform: str, mode: str) -> ValidationReport:
    report.kind, report.suggested_mode = "social", mode
    if mode in NOT_ACQUIRABLE:
        report.suggested_state = "rejected"
        report.notes.append(
            f"{platform}: {mode}; the importer never reads it (see list-sources --social)"
        )
    elif mode == "import_url":
        report.notes.append(f"{platform}: use `import-url <page>` for single event pages")
    else:
        report.notes.append(f"{platform}: add a verified account row to social_sources.yaml")
    return report


def _inconclusive(report: ValidationReport, message: str, *, transient: bool) -> ValidationReport:
    report.error, report.transient = mask_secrets(message), transient
    report.notes.append(report.error)
    return report


def _deny(report: ValidationReport, status: int | None, why: str) -> None:
    report.http_status = status
    report.blocked = why
    report.suggested_mode, report.suggested_state = "requires_authorization", "rejected"
    report.notes.append(f"{why}; not bypassed")


def validate_source(
    client: SafeHttpClient,
    url: str,
    *,
    now: datetime,
    source: SourceConfig | None = None,
    history: Sequence[Any] | None = None,
) -> ValidationReport:
    """Score ``url`` (or the configured ``source``) for use as an events source."""
    report = ValidationReport(url=url, source_id=source.id if source else None)
    social = None if source else classify_social_url(url)
    if social:
        return _social(report, *social)
    if history:
        health = summarize(history)
        report.reliability = {
            "runs": health["runs_considered"], "error_rate": health["error_rate"],
            "last_status": health["last_status"],
            "consecutive_failures": health["consecutive_failures"],
        }  # fmt: skip
    try:
        client.check_url(url)
    except (FetchError, ValueError) as exc:
        report.suggested_state = "rejected"
        report.notes.append(f"refused: {exc}")
        return report

    window = explicit_window(
        now.astimezone(TZ).date(), now.astimezone(TZ).date() + timedelta(days=60)
    )
    ctx = FetchContext(client=client, window=window, now=now, allow_render=False)
    body = b""
    result: SourceFetchResult | None
    try:
        if source is not None:
            report.kind = source.adapter
            result = create_adapter(source).fetch(ctx)
            report.reachable = True
        else:
            policy = RobotsPolicy(client)
            if not policy.allowed(url):
                state = policy.status(url)
                if state == "unavailable":
                    report.robots = "unavailable"
                    return _inconclusive(
                        report, "robots.txt could not be read: try again later", transient=True
                    )
                report.robots = "disallowed"
                report.suggested_mode, report.suggested_state = "excluded", "rejected"
                report.notes.append("robots.txt does not allow fetching this URL")
                return report
            report.robots = "allowed"
            probe = client.get(url)
            report.reachable, report.http_status = True, probe.status
            report.content_type = probe.headers.get("content-type")
            body = probe.content
            report.kind = _kind(report.content_type or "", body)
            result = _extract(report, ctx, url, body)
    except HttpError as exc:
        report.reachable, report.http_status = False, exc.status
        if exc.status in (401, 403):
            _deny(report, exc.status, f"access denied (HTTP {exc.status})")
            return report
        if exc.status in GONE_HTTP:
            report.suggested_mode, report.suggested_state = None, "rejected"
            report.notes.append(f"page not found (HTTP {exc.status}); check the URL")
            return report
        transient = exc.status is None or exc.status == TRANSIENT_HTTP or exc.status >= 500
        label = "temporary trouble" if transient else "cannot read the page"
        return _inconclusive(report, f"{label}: {exc}", transient=transient)
    except FetchError as exc:
        report.reachable = False
        return _inconclusive(report, f"cannot read the page: {exc}", transient=False)
    except Exception as exc:  # adapter or parser failure: a report with the error, no traceback
        logger.warning(
            "validate-source %s: %s: %s",
            mask_secrets(url),
            type(exc).__name__,
            mask_secrets(str(exc)),
        )
        return _inconclusive(report, f"{type(exc).__name__}: {exc}", transient=False)
    return _evaluate(report, result, body, now, source)


def _extract(
    report: ValidationReport, ctx: FetchContext, url: str, body: bytes
) -> SourceFetchResult | None:
    if report.kind == "ical":
        cfg = SourceConfig(id="validate", name="validate", adapter="ical", url=url)
        return IcalAdapter(cfg).read(body, ctx.window)
    if report.kind == "feed":
        cfg = SourceConfig(id="validate", name="validate", adapter="rss", url=url)
        return RssAdapter(cfg).read(body)
    if report.kind == "html":
        text = body.decode("utf-8", errors="replace")
        if is_challenge_page(text):
            _deny(report, report.http_status, "challenge page")
            report.reachable = False
            return None
        cfg = SourceConfig(
            id="validate", name="validate", adapter="web", url=url, params={"listing_urls": [url]}
        )
        return WebAdapter(cfg).fetch(ctx)
    report.notes.append(
        f"{report.kind}: not a format the importer reads without a dedicated adapter"
    )
    return None


def _evaluate(
    report: ValidationReport,
    result: SourceFetchResult | None,
    body: bytes,
    now: datetime,
    source: SourceConfig | None,
) -> ValidationReport:
    if report.blocked:
        return report
    events = result.events if result is not None else []
    if result is not None:
        report.strategy = result.strategy_used or (report.kind if events else None)
        report.notes.extend(result.warnings)
        if result.status in (BLOCKED, RATE_LIMITED, ROBOTS_DISALLOWED):
            report.reachable = False
            if result.status == RATE_LIMITED:
                return _inconclusive(report, "rate limited (HTTP 429)", transient=True)
            if result.status == BLOCKED:
                _deny(report, report.http_status, "the site refuses automated access")
            elif result.status == ROBOTS_DISALLOWED:
                report.robots = "disallowed"
                report.suggested_mode, report.suggested_state = "excluded", "rejected"
            return report
    report.events_found = len(events)
    if events:
        report.quality = round(sum(1 for e in events if _complete(e)) / len(events), 3)
        month = explicit_window(
            now.astimezone(TZ).date(), now.astimezone(TZ).date() + timedelta(days=COVERAGE_DAYS)
        )
        week = next_week(now)
        report.in_next_30_days = sum(1 for e in events if event_overlaps(month, e))
        report.in_target_week = sum(1 for e in events if event_overlaps(week, e))
    if source is not None and source.license and not source.license.lower().startswith("unknown"):
        report.terms = f"declared: {source.license}"
    elif body and (declared := _terms(body)):
        report.terms = declared
    if events or (report.reachable and report.kind in _FORMAT_KINDS):
        report.suggested_mode = _KIND_MODES.get(report.kind, "api_public")
    elif report.reachable:
        report.suggested_mode = "manual_only"
    verified = events and (report.quality or 0) >= MIN_QUALITY and report.in_next_30_days >= 1
    report.suggested_state = "verified" if verified else "candidate"
    report.score = _score(report)
    return report


def _score(report: ValidationReport) -> int:
    """0-100: presence 30, completeness 25, coverage 20, reliability 15, reuse terms 10."""
    if not report.reachable or report.blocked:
        return 0
    points = min(report.events_found, 3) * 10
    points += round((report.quality or 0) * 25)
    points += round(min(report.in_next_30_days, 10) / 10 * 20)
    if report.reliability and report.reliability["error_rate"] is not None:
        points += round((1 - report.reliability["error_rate"]) * 15)
    else:
        points += 8  # unknown history is neutral, not bad
    if report.terms != "unknown":
        points += 10
    return min(points, 100)
