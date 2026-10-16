from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from canarias_events.geo import ISLANDS
from hoy_api.publish.categories import CATEGORY_KEYS
from hoy_api.time_windows import Preset

FAR_FUTURE = date(9999, 12, 31)
MAX_RANGE_DAYS = 366
MAX_RADIUS_KM = 100.0
SortMode = Literal["relevance", "date", "distance"]


class EventSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    title: str
    start_at: datetime | None
    end_at: datetime | None
    start_date: date | None
    end_date: date | None
    all_day: bool
    timezone: str
    category: str
    island: str | None
    municipality: str | None
    venue_name: str | None
    is_free: bool | None
    price_min: float | None
    price_max: float | None
    currency: str | None
    image_url: str | None
    status: str
    stale: bool
    latitude: float | None
    longitude: float | None
    distance_km: float | None = None


class SourceLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_name: str
    source_url: str | None
    official_url: str | None


class EventDetail(EventSummary):
    description: str | None
    address: str | None
    tags: list[str]
    booking_url: str | None
    ticket_url: str | None
    official_event_url: str | None
    organizer_name: str | None
    organizer_url: str | None
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    last_verified_at: datetime | None
    updated_at: datetime
    sources: list[SourceLinkOut]


class EventPage(BaseModel):
    items: list[EventSummary]
    page: int
    limit: int
    total: int


class MapPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    title: str
    latitude: float
    longitude: float
    start_date: date | None
    start_at: datetime | None
    island: str | None
    category: str


class MapPage(BaseModel):
    items: list[MapPoint]
    truncated: bool


class CountItem(BaseModel):
    key: str
    count: int


class CountList(BaseModel):
    items: list[CountItem]


class EventQuery(BaseModel):
    """Validated query string for event search. Every bound here is enforced server-side."""

    q: str | None = Field(default=None, max_length=120)
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None
    preset: Preset | None = None
    island: str | None = None
    municipality: str | None = Field(default=None, max_length=120)
    category: str | None = None
    free: bool | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float | None = Field(default=None, gt=0, le=MAX_RADIUS_KM)
    sort: SortMode | None = None
    page: int = Field(default=1, ge=1, le=10_000)
    limit: int = Field(default=20, ge=1, le=50)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("q", "municipality", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("island")
    @classmethod
    def _known_island(cls, value: str | None) -> str | None:
        if value is not None and value not in ISLANDS:
            raise ValueError("unknown island")
        return value

    @field_validator("category")
    @classmethod
    def _known_category(cls, value: str | None) -> str | None:
        if value is not None and value not in CATEGORY_KEYS:
            raise ValueError("unknown category")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> EventQuery:
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be provided together")
        if self.radius_km is not None and self.lat is None:
            raise ValueError("radius_km requires lat and lon")
        if self.sort == "distance" and self.lat is None:
            raise ValueError("sort=distance requires lat and lon")
        if self.preset is not None and (self.from_ is not None or self.to is not None):
            raise ValueError("use either preset or from/to, not both")
        if self.from_ is not None and self.to is not None:
            if self.from_ > self.to:
                raise ValueError("from must be on or before to")
            if (self.to - self.from_).days > MAX_RANGE_DAYS:
                raise ValueError("date range is too wide")
        return self


class MapQuery(EventQuery):
    """Map search: the same filters plus a bounding box ``minLon,minLat,maxLon,maxLat``."""

    bbox: str

    @field_validator("bbox")
    @classmethod
    def _parse_bbox(cls, value: str) -> str:
        parts = value.split(",")
        if len(parts) != 4:
            raise ValueError("bbox needs four comma-separated numbers")
        try:
            min_lon, min_lat, max_lon, max_lat = (float(part) for part in parts)
        except ValueError as exc:
            raise ValueError("bbox values must be numbers") from exc
        if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
            raise ValueError("bbox must be ordered and inside the world bounds")
        return value

    def corners(self) -> tuple[float, float, float, float]:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in self.bbox.split(","))
        return min_lon, min_lat, max_lon, max_lat
