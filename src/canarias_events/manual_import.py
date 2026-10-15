"""One-off imports done by a person: ``import-url`` pages (and the review hold they share).

Nothing here is scheduled. A page is read through the same web adapter as the configured
portals (SSRF guard, robots.txt, content-type guard, JSON-LD, embedded JSON, then blocks) and
what it yields is held for review unless the person accepts it right away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from canarias_events.database import Database, source_key
from canarias_events.date_range import explicit_window
from canarias_events.http import SafeHttpClient
from canarias_events.models import Event
from canarias_events.pipeline import ingest_events
from canarias_events.sources.base import FetchContext, SourceConfig
from canarias_events.sources.web import WebAdapter
from canarias_events.statuses import HOLD_NEEDS_REVIEW

MAX_IMPORT_EVENTS = 25
# Placeholder URLs: these pseudo-sources have no feed; the rows only carry provenance/priority.
IMPORT_URL_SOURCE = SourceConfig(
    id="import_url", name="Imported event pages", adapter="web", priority=40,
    url="https://import-url.invalid/",
)  # fmt: skip
ORGANIZER_SOURCE = SourceConfig(
    id="organizer_submission", name="Organizer submissions", adapter="ical", priority=40,
    url="https://organizer-submission.invalid/",
)  # fmt: skip
# Facebook and Instagram are never read automatically: no scraping, private sessions, cookies
# or browsers. The organizer can send the event with ``submit-event`` instead.
REFUSED_HOSTS = ("facebook.com", "fb.com", "fb.me", "fb.watch", "instagram.com", "instagr.am")
REFUSAL = (
    "Facebook and Instagram pages are never read automatically (no scraping, no private "
    "sessions). Ask the organizer to send the event with `submit-event`, or enter it by hand."
)


class RefusedUrl(ValueError):
    """The URL belongs to a platform whose pages are never read."""


@dataclass
class UrlImport:
    events: list[Event] = field(default_factory=list)
    status: str = "ok"  # ok | blocked | rate_limited | robots_disallowed | needs_manual_review
    warnings: list[str] = field(default_factory=list)


def check_url(url: str) -> str:
    text = url.strip()
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise ValueError(f"not a valid http(s) URL: {url!r}") from None
    if parts.scheme not in ("http", "https") or not host:
        raise ValueError(f"not a valid http(s) URL: {url!r}")
    if any(host == h or host.endswith(f".{h}") for h in REFUSED_HOSTS):
        raise RefusedUrl(REFUSAL)
    return text


def import_url(client: SafeHttpClient, url: str, *, now: datetime) -> UrlImport:
    """Extract the events of one public page; nothing is stored and nothing is invented."""
    page = check_url(url)
    client.check_url(page)  # a private address fails here, not as a robots.txt problem
    host = urlsplit(page).hostname or ""
    cfg = SourceConfig(
        id=IMPORT_URL_SOURCE.id, name=f"Imported page ({host})", adapter="web", url=page,
        params={"listing_urls": [page]},
    )  # fmt: skip
    window = explicit_window(now.date(), now.date() + timedelta(days=365))
    ctx = FetchContext(client=client, window=window, now=now, allow_render=False)
    result = WebAdapter(cfg).fetch(ctx)
    outcome = UrlImport(status=result.status, warnings=list(result.warnings))
    found = result.events
    if len(found) > MAX_IMPORT_EVENTS:
        outcome.warnings.append(
            f"page lists {len(found)} events: kept the first {MAX_IMPORT_EVENTS}"
        )
        found = found[:MAX_IMPORT_EVENTS]
    for event in found:
        # A page with one event is that event's page; a listing page says nothing about each.
        official = event.official_event_url or (page if len(found) == 1 else None)
        extra: dict[str, Any] = {
            **event.extra, "provenance": IMPORT_URL_SOURCE.id, "source_page": page,
        }  # fmt: skip
        outcome.events.append(
            event.model_copy(
                update={
                    "source_name": cfg.name,
                    "source_url": page,
                    "source_type": "import_url",
                    "source_license": None,
                    "extra": extra,
                    "official_event_url": official,
                }
            )  # fmt: skip
        )
    return outcome


# --------------------------------------------------------------------------- review hold


def hold_for_review(db: Database, source: SourceConfig, events: list[Event], now: datetime) -> int:
    """Park ``events`` until a person accepts them (``review-quarantine --accept``).

    Earlier pending events of the source stay, and an event already waiting is not repeated.
    Returns how many were newly held.
    """
    db.sync_sources([source])
    waiting = {source_key(item.event) for item in db.quarantine_pending(source.id)}
    fresh = [e for e in events if source_key(e) not in waiting]
    return db.quarantine_add(source.id, 0, HOLD_NEEDS_REVIEW, fresh, now, replace=False)


def publish_now(
    db: Database, source: SourceConfig, events: list[Event], now: datetime
) -> dict[str, Any]:
    """Publish ``events`` through deduplication at once (a person said ``--accept``)."""
    db.sync_sources([source])
    return ingest_events(db, events, source.id, now)
