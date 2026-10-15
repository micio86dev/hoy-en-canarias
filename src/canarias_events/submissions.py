"""Organizer submissions: a voluntary, file-based way to hand us an event.

An organizer (or someone they trust) sends a JSON file that follows
``docs/organizer-submission.schema.json`` or an iCalendar file. The file is validated, nothing
is guessed, and the events wait for review (``review-quarantine``). No server, no e-mail.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from canarias_events.date_range import TZ, explicit_window
from canarias_events.geo import island_for_municipality, normalize_island, normalize_municipality
from canarias_events.manual_import import ORGANIZER_SOURCE
from canarias_events.models import Event
from canarias_events.sources.ical import IcalAdapter

SOURCE_TYPE = "organizer_submission"
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SubmissionError(ValueError):
    """The file cannot be accepted; ``errors`` says why, one line each."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class OrganizerEvent(BaseModel):
    """One event as an organizer describes it. Unknown keys are rejected, none are guessed.

    A file holds one event object, a JSON array of them, or {"events": [...]}.
    """

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, description="The organizer's own stable id (optional).")
    title: str = Field(min_length=1)
    start: datetime | date = Field(description="ISO 8601 date (all day) or date-time.")
    end: datetime | date | None = Field(default=None, description="Same form as start.")
    timezone: str = "Atlantic/Canary"
    description: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    organizer_name: str = Field(min_length=1)
    organizer_url: str | None = None
    contact_email: str | None = None
    venue_name: str | None = None
    address: str | None = None
    municipality: str | None = None
    island: str | None = None
    postal_code: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    price_min: float | None = Field(default=None, ge=0)
    price_max: float | None = Field(default=None, ge=0)
    currency: str | None = None
    is_free: bool | None = None
    url: str | None = Field(default=None, description="The event's own page (http or https).")
    ticket_url: str | None = None
    image_url: str | None = None

    @field_validator("title", "organizer_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("start", "end", mode="before")
    @classmethod
    def _iso(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            try:
                return date.fromisoformat(text) if len(text) == 10 else datetime.fromisoformat(text)
            except ValueError:
                raise ValueError("must be an ISO 8601 date or date-time") from None
        return value

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError):
            raise ValueError(f"unknown timezone {value!r}") from None
        return value

    @field_validator("url", "ticket_url", "image_url", "organizer_url")
    @classmethod
    def _http_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("must be an http(s) URL")
        return value.strip()

    @field_validator("contact_email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if value is not None and not _RE_EMAIL.match(value.strip()):
            raise ValueError("is not an e-mail address")
        return value.strip() if value else None

    @field_validator("municipality")
    @classmethod
    def _known_municipality(cls, value: str | None) -> str | None:
        if value is None:
            return None
        canonical = normalize_municipality(value)
        if canonical is None:
            raise ValueError(f"unknown municipality {value!r} (nothing is guessed)")
        return canonical

    @field_validator("island")
    @classmethod
    def _known_island(cls, value: str | None) -> str | None:
        if value is None:
            return None
        canonical = normalize_island(value)
        if canonical is None:
            raise ValueError(f"unknown island {value!r} (nothing is guessed)")
        return canonical

    @model_validator(mode="after")
    def _consistent(self) -> OrganizerEvent:
        start, end = self.start, self.end
        if end is not None:
            if isinstance(start, datetime) != isinstance(end, datetime):
                raise ValueError("end must have the same form as start (date or date-time)")
            if isinstance(start, datetime) and isinstance(end, datetime):
                before = _localize(end, self.timezone) < _localize(start, self.timezone)
            else:
                before = end < start
            if before:
                raise ValueError("end must not be before start")
        derived = island_for_municipality(self.municipality)
        if self.island and derived and self.island != derived:
            raise ValueError(
                f"island {self.island} does not match municipality {self.municipality}"
            )
        if self.price_min is not None and self.price_max is not None:
            if self.price_max < self.price_min:
                raise ValueError("price_max must not be below price_min")
        return self


def _localize(value: datetime, zone: str) -> datetime:
    """Date-times without an offset are in the event's own time zone."""
    return value.replace(tzinfo=ZoneInfo(zone)) if value.tzinfo is None else value


def _event_id(item: OrganizerEvent) -> str:
    if item.id:
        return item.id
    stamp = f"{item.organizer_name}|{item.title}|{item.start.isoformat()}"
    return "org:" + hashlib.sha1(stamp.encode()).hexdigest()[:16]


def to_event(item: OrganizerEvent) -> Event:
    start, end = item.start, item.end
    start_dt = _localize(start, item.timezone) if isinstance(start, datetime) else None
    end_dt = _localize(end, item.timezone) if isinstance(end, datetime) else None
    municipality = item.municipality
    return Event(
        title=item.title, description=item.description, category=item.category, tags=item.tags,
        start_datetime=start_dt, end_datetime=end_dt,
        start_date=None if start_dt else start,
        end_date=None if start_dt or end_dt or end is None else end,
        timezone=item.timezone, all_day=start_dt is None,
        venue_name=item.venue_name, address=item.address, municipality=municipality,
        island=item.island or island_for_municipality(municipality),
        postal_code=item.postal_code, latitude=item.latitude, longitude=item.longitude,
        price_min=item.price_min, price_max=item.price_max, currency=item.currency,
        is_free=item.is_free, official_event_url=item.url, ticket_url=item.ticket_url,
        image_url=item.image_url, organizer_name=item.organizer_name,
        organizer_url=item.organizer_url, contact_email=item.contact_email,
        source_name=f"Organizer: {item.organizer_name}", source_event_id=_event_id(item),
        source_type=SOURCE_TYPE, raw_data=item.model_dump(mode="json"),
        extra={"provenance": SOURCE_TYPE},
    )  # fmt: skip


def _items(data: Any) -> list[Any]:
    if isinstance(data, dict) and isinstance(data.get("events"), list):
        return list(data["events"])
    if isinstance(data, list):
        return data
    return [data]


def _from_json(text: str) -> list[Event]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SubmissionError([f"not valid JSON: {exc}"]) from exc
    events: list[Event] = []
    errors: list[str] = []
    for number, raw in enumerate(_items(data), start=1):
        try:
            events.append(to_event(OrganizerEvent.model_validate(raw)))
        except ValidationError as exc:
            for problem in exc.errors():
                where = ".".join(str(p) for p in problem["loc"]) or "event"
                errors.append(f"event {number}: {where}: {problem['msg']}")
    if errors:
        raise SubmissionError(errors)
    if not events:
        raise SubmissionError(["the file holds no events"])
    return events


def _from_ics(content: bytes, organizer: str | None, now: datetime) -> list[Event]:
    if not organizer or not organizer.strip():
        raise SubmissionError(["an .ics file does not name its organizer: pass --organizer <name>"])
    window = explicit_window(now.date(), now.date() + timedelta(days=366))
    try:
        result = IcalAdapter(ORGANIZER_SOURCE).read(content, window)
    except ValueError as exc:
        raise SubmissionError([f"not a valid iCalendar file: {exc}"]) from exc
    if result.discarded:
        raise SubmissionError([f"event skipped: {reason}" for reason, _ in result.discarded])
    if not result.events:
        raise SubmissionError(["no event in the next 12 months"])
    name = organizer.strip()
    return [
        e.model_copy(
            update={
                "organizer_name": name,
                "source_name": f"Organizer: {name}",
                "source_type": SOURCE_TYPE,
                "source_url": None,
                "source_license": None,
                "extra": {"provenance": SOURCE_TYPE},
            }
        )  # fmt: skip
        for e in result.events
    ]


def load_submission(path: Path, organizer: str | None, now: datetime) -> list[Event]:
    """Validated events of an organizer file (``.json`` or ``.ics``); all or nothing."""
    suffix = path.suffix.lower()
    if suffix not in (".json", ".ics"):
        raise SubmissionError(["unsupported file type: use .json or .ics"])
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise SubmissionError([f"cannot read {path}: {exc.strerror or exc}"]) from exc
    if suffix == ".ics":
        return _from_ics(content, organizer, now)
    try:
        return _from_json(content.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise SubmissionError(["the file is not UTF-8 text"]) from exc


__all__ = ["TZ", "OrganizerEvent", "SubmissionError", "load_submission", "to_event"]
