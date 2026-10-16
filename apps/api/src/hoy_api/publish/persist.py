from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from hoy_api.db.models import Event, EventSource
from hoy_api.publish.projection import ProjectedEvent
from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session


@dataclass
class PublishReport:
    total: int = 0
    inserted: int = 0
    updated: int = 0
    stale_marked: int = 0
    skipped: int = 0


def _apply(row: Event, projected: ProjectedEvent, now: datetime) -> None:
    """Copy every upstream-owned field. ``slug`` and ``published_at`` are never touched here."""
    row.title = projected.title
    row.description = projected.description
    row.category = projected.category
    row.category_raw = projected.category_raw
    row.tags = list(projected.tags)
    row.start_at = projected.start_at
    row.end_at = projected.end_at
    row.start_date = projected.start_date
    row.end_date = projected.end_date
    row.all_day = projected.all_day
    row.timezone = projected.timezone
    row.venue_name = projected.venue_name
    row.address = projected.address
    row.municipality = projected.municipality
    row.island = projected.island
    row.latitude = projected.latitude
    row.longitude = projected.longitude
    row.price_min = projected.price_min
    row.price_max = projected.price_max
    row.currency = projected.currency
    row.is_free = projected.is_free
    row.booking_url = projected.booking_url
    row.ticket_url = projected.ticket_url
    row.official_event_url = projected.official_event_url
    row.image_url = projected.image_url
    row.organizer_name = projected.organizer_name
    row.organizer_url = projected.organizer_url
    row.status = projected.status
    row.stale = False
    row.first_seen_at = row.first_seen_at or projected.first_seen_at
    row.last_seen_at = projected.last_seen_at
    row.last_verified_at = projected.last_verified_at or row.last_verified_at
    row.updated_at = now


SUBMISSION_PREFIX = "submission:"


def upsert_event(session: Session, projected: ProjectedEvent, now: datetime) -> tuple[UUID, bool]:
    """Insert or update one event by ``importer_id``. Returns (event id, inserted)."""
    existing_id = session.scalar(select(Event.id).where(Event.importer_id == projected.importer_id))
    if existing_id is None:
        row = Event(importer_id=projected.importer_id, slug=projected.slug, published_at=now)
        _apply(row, projected, now)
        session.add(row)
        session.flush()  # assigns the primary key used by the provenance rows below
        inserted = True
    else:
        loaded = session.get(Event, existing_id)
        if loaded is None:  # pragma: no cover - the id was just read in this transaction
            raise RuntimeError(f"event {existing_id} vanished during publish")
        row = loaded
        _apply(row, projected, now)
        inserted = False
    # Delete the old provenance rows before inserting the new ones: the unique key on
    # (event, source, source_event_id) would otherwise collide within one flush.
    session.execute(delete(EventSource).where(EventSource.event_id == row.id))
    session.add_all(
        EventSource(
            event_id=row.id,
            source_name=link.source_name,
            source_event_id=link.source_event_id,
            source_url=link.source_url,
            official_url=link.official_url,
        )
        for link in projected.sources
    )
    session.flush()
    return row.id, inserted


def publish_events(
    session: Session, events: Sequence[ProjectedEvent], now: datetime
) -> PublishReport:
    """Upsert projected events by ``importer_id``; never delete rows.

    Events missing from the current importer export are flagged ``stale`` so the API can
    still serve them with an honest label while the upstream source is unavailable.
    Visitor submissions are not part of the importer export and are never flagged.
    """
    report = PublishReport(total=len(events))
    for projected in events:
        _, inserted = upsert_event(session, projected, now)
        if inserted:
            report.inserted += 1
        else:
            report.updated += 1

    if not events:
        # An empty export usually means a failed upstream run, not that every event vanished.
        return report
    current = [projected.importer_id for projected in events]
    stale_result = session.execute(
        update(Event)
        .where(
            Event.importer_id.not_in(current),
            Event.importer_id.not_like(f"{SUBMISSION_PREFIX}%"),
            Event.stale.is_(False),
        )
        .values(stale=True, updated_at=now)
    )
    report.stale_marked = cast(CursorResult[Any], stale_result).rowcount
    return report
