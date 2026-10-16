from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from hoy_api.publish.categories import map_category
from hoy_api.publish.slug import make_slug

from canarias_events.geo import ISLANDS
from canarias_events.models import Event as ImportedEvent
from canarias_events.normalize import html_to_text

CANARY_TZ = ZoneInfo("Atlantic/Canary")
ALLOWED_ISLANDS = frozenset(ISLANDS)
ALLOWED_STATUSES = frozenset({"scheduled", "cancelled", "postponed", "rescheduled", "moved_online"})
UNKNOWN_STATUS = "unknown"


@dataclass(frozen=True)
class SourceLink:
    """One importer source record that was merged into a published event."""

    source_name: str
    source_event_id: str
    source_url: str | None
    official_url: str | None


@dataclass(frozen=True)
class ProjectedEvent:
    """An importer event shaped for the portal's Postgres schema."""

    importer_id: str
    slug: str
    title: str
    description: str | None
    category: str
    category_raw: str | None
    tags: tuple[str, ...]
    start_at: datetime | None
    end_at: datetime | None
    start_date: date | None
    end_date: date | None
    all_day: bool
    timezone: str
    venue_name: str | None
    address: str | None
    municipality: str | None
    island: str | None
    latitude: float | None
    longitude: float | None
    price_min: float | None
    price_max: float | None
    currency: str | None
    is_free: bool | None
    booking_url: str | None
    ticket_url: str | None
    official_event_url: str | None
    image_url: str | None
    organizer_name: str | None
    organizer_url: str | None
    status: str
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    last_verified_at: datetime | None
    sources: tuple[SourceLink, ...]


def safe_http_url(value: str | None) -> str | None:
    """Return the URL only when it is a plain http(s) link; otherwise None."""
    if not value:
        return None
    candidate = value.strip()
    if not candidate or any(ch.isspace() or ord(ch) < 32 for ch in candidate):
        return None
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return candidate


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=CANARY_TZ)


def _status(extra: dict[str, Any]) -> str:
    raw = extra.get("status")
    if not isinstance(raw, str):
        return UNKNOWN_STATUS
    normalized = raw.strip().lower().replace(" ", "_")
    return normalized if normalized in ALLOWED_STATUSES else UNKNOWN_STATUS


def _island(value: str | None) -> str | None:
    return value if value in ALLOWED_ISLANDS else None


def project_event(event: ImportedEvent, sources: list[SourceLink]) -> ProjectedEvent:
    """Project one importer event. Requires ``event.id``; the caller filters undated events."""
    if event.id is None:
        raise ValueError("cannot project an importer event without an id")
    description = html_to_text(event.description) or None
    return ProjectedEvent(
        importer_id=event.id,
        slug=make_slug(event.title, event.id),
        title=event.title.strip(),
        description=description,
        category=map_category(event.category, event.tags),
        category_raw=event.category,
        tags=tuple(event.tags),
        start_at=_aware(event.start_datetime),
        end_at=_aware(event.end_datetime),
        start_date=event.start_date,
        end_date=event.end_date,
        all_day=event.all_day,
        timezone=event.timezone,
        venue_name=event.venue_name,
        address=event.address,
        municipality=event.municipality,
        island=_island(event.island),
        latitude=event.latitude,
        longitude=event.longitude,
        price_min=event.price_min,
        price_max=event.price_max,
        currency=event.currency,
        is_free=event.is_free,
        booking_url=safe_http_url(event.booking_url),
        ticket_url=safe_http_url(event.ticket_url),
        official_event_url=safe_http_url(event.official_event_url),
        image_url=safe_http_url(event.image_url),
        organizer_name=event.organizer_name,
        organizer_url=safe_http_url(event.organizer_url),
        status=_status(event.extra),
        first_seen_at=_aware(event.first_seen_at),
        last_seen_at=_aware(event.last_seen_at),
        last_verified_at=_aware(event.last_verified_at),
        sources=tuple(sources),
    )
