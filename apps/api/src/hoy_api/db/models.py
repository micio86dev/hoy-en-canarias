from datetime import date, datetime
from typing import Any
from uuid import UUID

from hoy_api.db.base import Base
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Mirrors the generated column in migration 0002; the database computes it on write.
SEARCH_TSV_EXPRESSION = (
    "setweight(to_tsvector('simple', hoy_unaccent(coalesce(title, ''))), 'A') || "
    "setweight(to_tsvector('simple', hoy_unaccent("
    "coalesce(venue_name, '') || ' ' || coalesce(municipality, ''))), 'B') || "
    "setweight(to_tsvector('simple', hoy_unaccent(coalesce(description, ''))), 'C')"
)


class Event(Base):
    """A published event. Mirrors migration 0002."""

    __tablename__ = "events"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    importer_id: Mapped[str] = mapped_column(Text, unique=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text, server_default=text("'other'"))
    category_raw: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    all_day: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    timezone: Mapped[str] = mapped_column(Text, server_default=text("'Atlantic/Canary'"))
    venue_name: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    municipality: Mapped[str | None] = mapped_column(Text)
    island: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Double)
    longitude: Mapped[float | None] = mapped_column(Double)
    price_min: Mapped[float | None] = mapped_column(Numeric(10, 2, asdecimal=False))
    price_max: Mapped[float | None] = mapped_column(Numeric(10, 2, asdecimal=False))
    currency: Mapped[str | None] = mapped_column(Text)
    is_free: Mapped[bool | None] = mapped_column(Boolean)
    booking_url: Mapped[str | None] = mapped_column(Text)
    ticket_url: Mapped[str | None] = mapped_column(Text)
    official_event_url: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    organizer_name: Mapped[str | None] = mapped_column(Text)
    organizer_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'unknown'"))
    stale: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(SEARCH_TSV_EXPRESSION, persisted=True),
        deferred=True,
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    sources: Mapped[list["EventSource"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", passive_deletes=True
    )


class EventSource(Base):
    """Provenance: one importer source record merged into a published event."""

    __tablename__ = "event_sources"
    __table_args__ = (UniqueConstraint("event_id", "source_name", "source_event_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    source_name: Mapped[str] = mapped_column(Text)
    source_event_id: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    official_url: Mapped[str | None] = mapped_column(Text)

    event: Mapped[Event] = relationship(back_populates="sources")


class ImportRun(Base):
    """One importer cycle: who triggered it, the window, attempts, outcome and counts."""

    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trigger: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    events_inserted: Mapped[int | None] = mapped_column(Integer)
    events_updated: Mapped[int | None] = mapped_column(Integer)
    events_stale: Mapped[int | None] = mapped_column(Integer)
    events_skipped: Mapped[int | None] = mapped_column(Integer)
    error_summary: Mapped[str | None] = mapped_column(Text)


class Submission(Base):
    """A visitor-submitted event. Only ``approved`` submissions are published."""

    __tablename__ = "submissions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    status: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventAttendance(Base):
    """A guest's "I'm going" for an event. Only a SHA-256 of the guest identifier is stored."""

    __tablename__ = "event_attendances"
    __table_args__ = (
        UniqueConstraint("event_id", "guest_hash", name="event_attendances_event_guest_key"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE")
    )
    guest_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
