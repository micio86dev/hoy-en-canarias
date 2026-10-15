"""Canonical event model shared by every adapter, the database and the exporters."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

from canarias_events.date_range import TZ

_URL_FIELDS = (
    "booking_url", "ticket_url", "official_event_url", "image_url", "organizer_url", "source_url",
)  # fmt: skip
_TEXT_FIELDS = (
    "id", "description", "summary", "category", "venue_name", "address", "municipality", "island",
    "postal_code", "currency", "organizer_name", "contact_email", "source_event_id",
    "source_type", "source_license",
)  # fmt: skip
_DATETIME_FIELDS = (
    "start_datetime", "end_datetime", "first_seen_at", "last_seen_at", "imported_at",
    "last_verified_at",
)  # fmt: skip
# Volatile or bulky fields never take part in the content hash.
_HASH_EXCLUDED = frozenset(
    {"id", "first_seen_at", "last_seen_at", "imported_at", "last_verified_at", "raw_data"}
)


class Event(BaseModel):
    id: str | None = None
    title: str
    description: str | None = None
    summary: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)

    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    start_date: date | None = None
    end_date: date | None = None
    timezone: str = "Atlantic/Canary"
    all_day: bool = False

    venue_name: str | None = None
    address: str | None = None
    municipality: str | None = None
    island: str | None = None
    postal_code: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)

    price_min: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    price_max: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = None
    is_free: bool | None = None

    booking_url: str | None = None
    ticket_url: str | None = None
    official_event_url: str | None = None
    image_url: str | None = None
    organizer_name: str | None = None
    organizer_url: str | None = None
    contact_email: str | None = None

    source_name: str
    source_url: str | None = None
    source_event_id: str | None = None
    source_type: str | None = None
    source_license: str | None = None

    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    imported_at: datetime | None = None
    last_verified_at: datetime | None = None

    raw_data: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------ validators

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be empty")
        return stripped

    @field_validator(*_TEXT_FIELDS, mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator(*_URL_FIELDS, mode="before")
    @classmethod
    def _http_urls_only(cls, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        try:
            parts = urlsplit(candidate)
            host = parts.hostname
        except ValueError:
            return None
        if parts.scheme.lower() in ("http", "https") and host:
            return candidate
        return None

    @field_validator("tags", mode="before")
    @classmethod
    def _clean_tags(cls, value: object) -> object:
        if not isinstance(value, list | tuple):
            return value
        seen: dict[str, None] = {}
        for item in value:
            if isinstance(item, str) and item.strip():
                seen.setdefault(item.strip(), None)
        return list(seen)

    @field_validator("timezone", mode="before")
    @classmethod
    def _valid_timezone(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "Atlantic/Canary"
        if isinstance(value, str):
            try:
                ZoneInfo(value.strip())
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError(f"unknown timezone: {value}") from exc
            return value.strip()
        return value

    @field_validator(*_DATETIME_FIELDS)
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=TZ)
        return value

    @model_validator(mode="after")
    def _derive_dates(self) -> Event:
        zone = ZoneInfo(self.timezone)
        if self.start_date is None and self.start_datetime is not None:
            self.start_date = self.start_datetime.astimezone(zone).date()
        if self.end_date is None and self.end_datetime is not None:
            self.end_date = self.end_datetime.astimezone(zone).date()
        return self

    # ------------------------------------------------------------------ hashing

    def content_hash(self) -> str:
        """SHA-256 over meaningful content; ignores timestamps, id and raw_data."""
        payload = {
            name: _hashable(getattr(self, name))
            for name in type(self).model_fields
            if name not in _HASH_EXCLUDED
        }
        blob = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _hashable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(TZ).isoformat() if value.tzinfo else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list):
        return sorted(_hashable(v) for v in value)
    return value
