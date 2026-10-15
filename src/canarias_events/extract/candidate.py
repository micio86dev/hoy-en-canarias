"""Intermediate record produced by every extraction strategy before it becomes an ``Event``."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

Confidence = Literal["high", "medium", "low"]

RAW_LIMIT = 4096


@dataclass
class CandidateEvent:
    """One event found on a page. Missing facts stay None; nothing is guessed."""

    title: str
    start_date: date | None = None
    end_date: date | None = None
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    description: str | None = None
    category: str | None = None
    venue_name: str | None = None
    address: str | None = None
    municipality: str | None = None
    postal_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    price_min: float | None = None
    price_max: float | None = None
    currency: str | None = None
    is_free: bool | None = None
    ticket_url: str | None = None
    official_event_url: str | None = None
    image_url: str | None = None
    organizer_name: str | None = None
    organizer_url: str | None = None
    strategy: str = ""
    confidence: Confidence = "medium"
    year_inferred: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def first_day(self) -> date | None:
        if self.start_datetime is not None:
            return self.start_datetime.date()
        return self.start_date

    @property
    def last_day(self) -> date | None:
        if self.end_datetime is not None:
            return self.end_datetime.date()
        return self.end_date

    def identity(self) -> tuple[str, str]:
        """Stable key used to drop duplicates found by the same strategy."""
        if self.official_event_url:
            return (
                "url",
                self.official_event_url.rstrip("/").casefold() + "|" + str(self.first_day),
            )
        return ("title", f"{self.title.casefold()}|{self.first_day}")

    def richness(self) -> int:
        """How many optional facts the candidate carries (used to keep the best duplicate)."""
        names = (
            "description", "category", "venue_name", "address", "municipality", "image_url",
            "price_min", "ticket_url", "official_event_url", "start_datetime", "end_datetime",
            "end_date", "organizer_name",
        )  # fmt: skip
        return sum(1 for n in names if getattr(self, n) is not None)


def dedupe(candidates: list[CandidateEvent]) -> list[CandidateEvent]:
    """Drop duplicates (same URL and day, or same title and day), keeping the richest one."""
    best: dict[tuple[str, str], CandidateEvent] = {}
    order: list[tuple[str, str]] = []
    for cand in candidates:
        key = cand.identity()
        if key not in best:
            best[key] = cand
            order.append(key)
        elif cand.richness() > best[key].richness():
            best[key] = cand
    return [best[k] for k in order]


def fragment(value: Any, limit: int = RAW_LIMIT) -> dict[str, Any]:
    """Bounded copy of the source fragment for ``raw_data`` (never a whole page)."""
    if isinstance(value, str):
        return {"text": value[:limit]}
    try:
        blob = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        return {"text": str(value)[:limit]}
    if len(blob) <= limit and isinstance(value, dict):
        return dict(value)
    return {"text": blob[:limit], "truncated": len(blob) > limit}


def resolve_url(base: str | None, value: object) -> str | None:
    """Absolute http(s) URL, or None (``javascript:``, ``data:``, mailto and junk are dropped)."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or candidate.startswith("#"):
        return None
    try:
        absolute = urljoin(base, candidate) if base else candidate
        parts = urlsplit(absolute)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    return absolute
