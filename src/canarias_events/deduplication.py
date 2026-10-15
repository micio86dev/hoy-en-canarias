"""Deterministic event matching (no AI): source id, official URL, then fuzzy title + place.

Order of the rules for an incoming event:

1. Same ``(source_id, source_event_id)``: it is the same event (an update).
2. Same canonical official URL and same start date, from another source: same event.
3. Same start/end dates, similar title, agreeing location and compatible start time: merge.
   Near misses (similar title but no location agreement, or a lower score) are kept as separate
   events and reported as possible duplicates.

Two events of the same source are never merged automatically.
"""

from __future__ import annotations

import math

from rapidfuzz import fuzz

from canarias_events.database import (
    Candidate,
    Database,
    Decision,
    PossibleDuplicate,
    source_key,
)
from canarias_events.date_range import TZ
from canarias_events.geo import normalize_municipality
from canarias_events.models import Event
from canarias_events.normalize import normalize_title

DEFAULT_MERGE_THRESHOLD = 92.0
REVIEW_THRESHOLD = 75.0
MAX_DISTANCE_M = 300.0
VENUE_THRESHOLD = 85.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def title_score(a: Event, b: Event) -> float:
    return float(fuzz.token_sort_ratio(normalize_title(a.title), normalize_title(b.title)))


def _municipality(event: Event) -> str | None:
    if not event.municipality:
        return None
    return normalize_municipality(event.municipality) or normalize_title(event.municipality)


def location_agrees(a: Event, b: Event) -> bool:
    """Same municipality, coordinates within 300 m, or two similar venue names."""
    ma, mb = _municipality(a), _municipality(b)
    if ma and mb and ma == mb:
        return True
    if None not in (a.latitude, a.longitude, b.latitude, b.longitude) and (
        haversine_m(a.latitude or 0.0, a.longitude or 0.0, b.latitude or 0.0, b.longitude or 0.0)
        <= MAX_DISTANCE_M
    ):
        return True
    if a.venue_name and b.venue_name:
        score = fuzz.token_sort_ratio(normalize_title(a.venue_name), normalize_title(b.venue_name))
        return float(score) >= VENUE_THRESHOLD
    return False


def times_compatible(a: Event, b: Event) -> bool:
    """Same local start time, or one side is all-day / has no time."""
    if a.all_day or b.all_day or a.start_datetime is None or b.start_datetime is None:
        return True
    la, lb = a.start_datetime.astimezone(TZ), b.start_datetime.astimezone(TZ)
    return (la.hour, la.minute) == (lb.hour, lb.minute)


def same_dates(a: Event, b: Event) -> bool:
    if a.start_date is None or a.start_date != b.start_date:
        return False
    return (a.end_date or a.start_date) == (b.end_date or b.start_date)


def resolve(
    db: Database,
    event: Event,
    source_id: str,
    *,
    merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
) -> Decision:
    """Decide whether ``event`` updates a known record, merges into another event, or is new."""
    if db.find_link(source_id, source_key(event)) is not None:
        return Decision("existing")

    if event.official_event_url:
        for cand in db.candidates_by_url(event.official_event_url):
            if source_id not in cand.source_ids and cand.event.start_date == event.start_date:
                return Decision("merge", cand.event.id, "url")

    if event.start_date is None:
        return Decision("new")

    best: tuple[float, Candidate] | None = None
    possibles: list[PossibleDuplicate] = []
    for cand in db.candidates_on(event.start_date):
        if cand.event.id is None or not same_dates(event, cand.event):
            continue
        score = title_score(event, cand.event)
        if source_id in cand.source_ids:
            if score >= merge_threshold:
                possibles.append(
                    PossibleDuplicate(cand.event.id, score, "same source, never auto-merged")
                )
            continue
        if score >= merge_threshold:
            if not location_agrees(event, cand.event):
                possibles.append(
                    PossibleDuplicate(cand.event.id, score, "similar title, location differs")
                )
            elif not times_compatible(event, cand.event):
                possibles.append(
                    PossibleDuplicate(cand.event.id, score, "similar title, start time differs")
                )
            elif best is None or score > best[0]:
                best = (score, cand)
        elif score >= REVIEW_THRESHOLD:
            possibles.append(
                PossibleDuplicate(cand.event.id, score, f"title similarity {score:.0f}")
            )
    if best is not None and best[1].event.id is not None:
        return Decision("merge", best[1].event.id, "fuzzy")
    return Decision("new", possibles=tuple(possibles))
