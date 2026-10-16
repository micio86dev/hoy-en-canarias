from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Date, Float, cast, func, literal, or_, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql.elements import ColumnElement

from canarias_events.geo import ISLANDS
from hoy_api.db.models import Event
from hoy_api.publish.categories import CATEGORY_KEYS
from hoy_api.schemas import FAR_FUTURE
from hoy_api.time_windows import CANARY_TZ_NAME

# Local calendar days in the Canary Islands. An event spans from its first to its last day.
LOCAL_START = func.coalesce(
    cast(func.timezone(CANARY_TZ_NAME, Event.start_at), Date), Event.start_date
)
LOCAL_END = func.coalesce(
    cast(func.timezone(CANARY_TZ_NAME, Event.end_at), Date), Event.end_date, LOCAL_START
)
EARTH_RADIUS_KM = 6371.0
SIMILARITY_THRESHOLD = 0.3


@dataclass(frozen=True)
class EventFilters:
    from_date: date
    to_date: date = FAR_FUTURE
    q: str | None = None
    island: str | None = None
    municipality: str | None = None
    category: str | None = None
    free_only: bool = False
    lat: float | None = None
    lon: float | None = None
    radius_km: float | None = None
    bbox: tuple[float, float, float, float] | None = None


def _tsquery(text: str) -> Any:
    return func.websearch_to_tsquery("simple", func.hoy_unaccent(text))


def _distance_km(lat: float, lon: float) -> ColumnElement[float]:
    """Great-circle distance in kilometres, computed in SQL (haversine)."""
    half_dlat = func.radians(Event.latitude - lat) / 2
    half_dlon = func.radians(Event.longitude - lon) / 2
    a = func.power(func.sin(half_dlat), 2) + (
        func.cos(func.radians(lat))
        * func.cos(func.radians(Event.latitude))
        * func.power(func.sin(half_dlon), 2)
    )
    return 2 * EARTH_RADIUS_KM * func.asin(func.sqrt(a))


def _conditions(filters: EventFilters) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [
        LOCAL_END >= filters.from_date,
        LOCAL_START <= filters.to_date,
    ]
    if filters.island is not None:
        conditions.append(Event.island == filters.island)
    if filters.municipality is not None:
        conditions.append(func.lower(Event.municipality) == filters.municipality.lower())
    if filters.category is not None:
        conditions.append(Event.category == filters.category)
    if filters.free_only:
        conditions.append(Event.is_free.is_(True))
    if filters.q is not None:
        conditions.append(
            or_(
                Event.search_tsv.op("@@")(_tsquery(filters.q)),
                func.similarity(func.hoy_unaccent(Event.title), func.hoy_unaccent(filters.q))
                > SIMILARITY_THRESHOLD,
            )
        )
    if filters.lat is not None and filters.lon is not None and filters.radius_km is not None:
        conditions.append(Event.latitude.isnot(None))
        conditions.append(_distance_km(filters.lat, filters.lon) <= filters.radius_km)
    if filters.bbox is not None:
        min_lon, min_lat, max_lon, max_lat = filters.bbox
        conditions.extend(
            [
                Event.latitude.isnot(None),
                Event.longitude.isnot(None),
                Event.longitude.between(min_lon, max_lon),
                Event.latitude.between(min_lat, max_lat),
            ]
        )
    return conditions


def search_events(
    session: Session, filters: EventFilters, page: int, limit: int, sort: str
) -> tuple[list[tuple[Event, float | None]], int]:
    conditions = _conditions(filters)
    distance: ColumnElement[float] | None = None
    if filters.lat is not None and filters.lon is not None:
        distance = _distance_km(filters.lat, filters.lon)

    count_stmt = select(func.count()).select_from(select(Event.id).where(*conditions).subquery())
    total = int(session.scalar(count_stmt) or 0)

    order: list[Any]
    if sort == "relevance" and filters.q is not None:
        query = _tsquery(filters.q)
        order = [
            func.ts_rank_cd(Event.search_tsv, query).desc(),
            func.similarity(func.hoy_unaccent(Event.title), func.hoy_unaccent(filters.q)).desc(),
            LOCAL_START,
            Event.id,
        ]
    elif sort == "distance" and distance is not None:
        order = [distance.asc().nulls_last(), LOCAL_START, Event.id]
    else:
        order = [LOCAL_START, Event.id]

    distance_column = (
        distance.label("distance_km") if distance is not None else cast(literal(None), Float)
    )
    stmt = (
        select(Event, distance_column)
        .where(*conditions)
        .order_by(*order)
        .offset((page - 1) * limit)
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    return [(row[0], row[1]) for row in rows], total


def get_event_by_slug(session: Session, slug: str) -> Event | None:
    return session.scalars(
        select(Event).where(Event.slug == slug).options(selectinload(Event.sources))
    ).one_or_none()


def related_events(session: Session, event: Event, today: date, limit: int = 6) -> list[Event]:
    same = Event.category == event.category
    if event.island is not None:
        same = or_(same, Event.island == event.island)
    stmt = (
        select(Event)
        .where(Event.id != event.id, LOCAL_END >= today, same)
        .order_by(LOCAL_START, Event.id)
        .limit(limit)
    )
    return list(session.scalars(stmt).all())


def island_counts(session: Session, today: date) -> dict[str, int]:
    stmt = (
        select(Event.island, func.count())
        .where(LOCAL_END >= today, Event.island.isnot(None))
        .group_by(Event.island)
    )
    counts = {island: count for island, count in session.execute(stmt).all() if island}
    return {island: counts.get(island, 0) for island in ISLANDS}


def category_counts(session: Session, today: date) -> dict[str, int]:
    stmt = select(Event.category, func.count()).where(LOCAL_END >= today).group_by(Event.category)
    counts = {category: count for category, count in session.execute(stmt).all()}
    return {category: counts.get(category, 0) for category in CATEGORY_KEYS}


def municipality_counts(session: Session, today: date, island: str | None) -> list[tuple[str, int]]:
    conditions: list[ColumnElement[bool]] = [LOCAL_END >= today, Event.municipality.isnot(None)]
    if island is not None:
        conditions.append(Event.island == island)
    stmt = (
        select(Event.municipality, func.count())
        .where(*conditions)
        .group_by(Event.municipality)
        .order_by(func.count().desc(), Event.municipality)
    )
    return [(name, count) for name, count in session.execute(stmt).all() if name]


def map_events(session: Session, filters: EventFilters, limit: int) -> tuple[list[Event], bool]:
    stmt = (
        select(Event).where(*_conditions(filters)).order_by(LOCAL_START, Event.id).limit(limit + 1)
    )
    rows = list(session.scalars(stmt).all())
    return rows[:limit], len(rows) > limit
