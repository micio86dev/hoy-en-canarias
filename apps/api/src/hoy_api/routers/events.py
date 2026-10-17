from dataclasses import replace
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from hoy_api.db.models import Event
from hoy_api.deps import ClockDep, SessionDep, enforce_rate_limit
from hoy_api.repositories.attendance import (
    attendee_count,
    guest_hash,
    is_attending,
    join,
    leave,
)
from hoy_api.repositories.events import (
    EventFilters,
    get_event_by_slug,
    map_events,
    related_events,
    search_events,
)
from hoy_api.schemas import (
    FAR_FUTURE,
    AttendanceState,
    EventDetail,
    EventPage,
    EventQuery,
    EventSummary,
    MapPage,
    MapPoint,
    MapQuery,
)
from hoy_api.time_windows import local_today, preset_window

router = APIRouter(prefix="/events", tags=["events"], dependencies=[Depends(enforce_rate_limit)])


def _filters(query: EventQuery, today: date) -> EventFilters:
    if query.preset is not None:
        start, end = preset_window(query.preset, today)
    else:
        start = query.from_ or today
        end = query.to or FAR_FUTURE
    return EventFilters(
        from_date=start,
        to_date=end,
        q=query.q,
        island=query.island,
        municipality=query.municipality,
        category=query.category,
        free_only=bool(query.free),
        lat=query.lat,
        lon=query.lon,
        radius_km=query.radius_km,
    )


def _summary(event: Event, distance: float | None) -> EventSummary:
    summary = EventSummary.model_validate(event)
    summary.distance_km = None if distance is None else round(distance, 3)
    return summary


@router.get("", response_model=EventPage)
def list_events(
    query: Annotated[EventQuery, Query()],
    session: SessionDep,
    now: ClockDep,
) -> EventPage:
    today = local_today(now)
    filters = _filters(query, today)
    sort = query.sort or ("relevance" if query.q else "date")
    rows, total = search_events(session, filters, query.page, query.limit, sort)
    return EventPage(
        items=[_summary(event, distance) for event, distance in rows],
        page=query.page,
        limit=query.limit,
        total=total,
    )


@router.get("/map", response_model=MapPage)
def map_points(
    query: Annotated[MapQuery, Query()],
    session: SessionDep,
    now: ClockDep,
) -> MapPage:
    today = local_today(now)
    filters = replace(_filters(query, today), bbox=query.corners())
    events, truncated = map_events(session, filters, limit=1000)
    return MapPage(items=[MapPoint.model_validate(event) for event in events], truncated=truncated)


@router.get("/{slug}", response_model=EventDetail)
def event_detail(slug: str, session: SessionDep) -> EventDetail:
    event = get_event_by_slug(session, slug)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    detail = EventDetail.model_validate(event)
    return detail.model_copy(update={"attendee_count": attendee_count(session, event.id)})


GuestHeader = Annotated[UUID, Header(alias="X-Guest-Id")]


def _state(session: SessionDep, event: Event, guest: UUID) -> AttendanceState:
    digest = guest_hash(guest)
    return AttendanceState(
        attendees=attendee_count(session, event.id),
        attending=is_attending(session, event.id, digest),
    )


def _event_or_404(session: SessionDep, slug: str) -> Event:
    event = get_event_by_slug(session, slug)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.get("/{slug}/attendance", response_model=AttendanceState)
def attendance_state(slug: str, guest: GuestHeader, session: SessionDep) -> AttendanceState:
    return _state(session, _event_or_404(session, slug), guest)


@router.post("/{slug}/attendance", response_model=AttendanceState)
def join_event(slug: str, guest: GuestHeader, session: SessionDep) -> AttendanceState:
    event = _event_or_404(session, slug)
    join(session, event.id, guest_hash(guest))
    session.commit()
    return _state(session, event, guest)


@router.delete("/{slug}/attendance", response_model=AttendanceState)
def leave_event(slug: str, guest: GuestHeader, session: SessionDep) -> AttendanceState:
    event = _event_or_404(session, slug)
    leave(session, event.id, guest_hash(guest))
    session.commit()
    return _state(session, event, guest)


@router.get("/{slug}/related", response_model=EventPage)
def event_related(slug: str, session: SessionDep, now: ClockDep) -> EventPage:
    event = get_event_by_slug(session, slug)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    items = related_events(session, event, local_today(now))
    summaries = [_summary(item, None) for item in items]
    return EventPage(items=summaries, page=1, limit=len(summaries) or 1, total=len(summaries))
