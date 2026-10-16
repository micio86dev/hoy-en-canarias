"""Deterministic event matching (no AI): source id, official URL, then fuzzy title + place.

Order of the rules for an incoming event:

1. Same ``(source_id, source_event_id)``: it is the same event (an update).
2. Same canonical official URL and same start date, from another source: same event. From
   the same source it is a republished copy only when start and end are exactly the same.
3. Same start/end dates, similar title, agreeing location and compatible start time: merge.
   Near misses (similar title but no location agreement, a lower score, or the same start with
   another end date) are kept as separate events and reported as possible duplicates. A title
   suffix naming the venue (``Show - Teatro Leal``) is ignored when comparing titles.

Apart from such exact copies, two events of the same source are never merged. Events read
from social posts (``extra.source_trust == "social"``) skip rule 3 on either side: a near match
is only reported as a possible duplicate. Rules 1 and 2 are exact identities and still apply;
field conflicts are settled by source priority, which social sources keep low (see
``social.SOCIAL_PRIORITY``).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

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
from canarias_events.models import Event, is_social
from canarias_events.normalize import normalize_title

DEFAULT_MERGE_THRESHOLD = 92.0
REVIEW_THRESHOLD = 75.0
MAX_DISTANCE_M = 300.0
VENUE_THRESHOLD = 85.0

_SUFFIX_SEPARATOR = re.compile(r"\s+[-\u2013\u2014|]\s+")


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def without_venue_suffix(title: str, venues: list[str]) -> str:
    """Drop a trailing `` - <venue>`` segment (``Show - Teatro Leal de La Laguna``)."""
    parts = _SUFFIX_SEPARATOR.split(title)
    if len(parts) < 2:
        return title
    tail = normalize_title(parts[-1])
    for venue in venues:
        name = normalize_title(venue)
        if name and (tail == name or tail.startswith(name + " ")):
            return " - ".join(parts[:-1])
    return title


def title_score(a: Event, b: Event) -> float:
    """Title similarity, also comparing the titles without a suffix naming either venue."""
    plain = fuzz.token_sort_ratio(normalize_title(a.title), normalize_title(b.title))
    venues = [v for v in (a.venue_name, b.venue_name) if v]
    if not venues:
        return float(plain)
    bare = fuzz.token_sort_ratio(
        normalize_title(without_venue_suffix(a.title, venues)),
        normalize_title(without_venue_suffix(b.title, venues)),
    )
    return float(max(plain, bare))


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


def same_occurrence(a: Event, b: Event) -> bool:
    """Exactly the same start and end, for a source that publishes one event twice."""
    return (a.start_date, a.start_datetime, a.end_date, a.end_datetime, a.all_day) == (
        b.start_date,
        b.start_datetime,
        b.end_date,
        b.end_datetime,
        b.all_day,
    )


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
            if source_id in cand.source_ids:
                if same_occurrence(event, cand.event):
                    return Decision("merge", cand.event.id, "url")
            elif cand.event.start_date == event.start_date:
                return Decision("merge", cand.event.id, "url")

    if event.start_date is None:
        return Decision("new")

    best: tuple[float, Candidate] | None = None
    possibles: list[PossibleDuplicate] = []
    for cand in db.candidates_on(event.start_date):
        if cand.event.id is None or cand.event.start_date != event.start_date:
            continue
        score = title_score(event, cand.event)
        if not same_dates(event, cand.event):
            if score >= REVIEW_THRESHOLD and source_id not in cand.source_ids:
                possibles.append(
                    PossibleDuplicate(cand.event.id, score, "same start date, end date differs")
                )
            continue
        if source_id in cand.source_ids:
            if score >= merge_threshold:
                possibles.append(
                    PossibleDuplicate(cand.event.id, score, "same source, never auto-merged")
                )
            continue
        if score >= merge_threshold:
            if is_social(event) or is_social(cand.event):
                possibles.append(
                    PossibleDuplicate(cand.event.id, score, "social source, never auto-merged")
                )
            elif not location_agrees(event, cand.event):
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


@dataclass(frozen=True)
class CopyPair:
    """Two stored events of one source that are the same occurrence (see ``same_occurrence``)."""

    keep_id: str
    drop_id: str
    source_id: str
    url: str
    skipped_reason: str | None = None


def _first_seen(event: Event) -> str:
    return event.first_seen_at.isoformat() if event.first_seen_at else ""


def find_same_source_copies(db: Database) -> list[CopyPair]:
    """Pairs of events one source published twice (same canonical URL, same start and end).

    The kept event has the most links, then the older ``first_seen_at``, then the smaller id.
    A copy with more than one link has data from several places and is reported as skipped.
    Each event is in at most one pair, so a run never touches the same row twice.
    """
    pairs: list[CopyPair] = []
    used: set[str] = set()
    for source_id, url, event_ids in db.shared_url_groups():
        events = {e.id: e for i in event_ids if (e := db.get_event(i)) is not None and e.id}
        counts = {i: db.link_counts(i) for i in events}
        order = sorted(events, key=lambda i: (-counts[i][0], _first_seen(events[i]), i))
        for keep_id in order:
            if keep_id in used:
                continue
            for drop_id in order:
                if drop_id == keep_id or drop_id in used:
                    continue
                if not same_occurrence(events[keep_id], events[drop_id]):
                    continue
                used.add(drop_id)
                links, sources = counts[drop_id]
                reason = None
                if links > 1:
                    reason = (
                        f"copy has links from {sources} sources"
                        if sources > 1
                        else f"copy has {links} links"
                    )
                pairs.append(CopyPair(keep_id, drop_id, source_id, url, reason))
            used.add(keep_id)
    return pairs
