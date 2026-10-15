"""schema.org Event extraction from JSON-LD scripts and (secondarily) microdata."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from canarias_events.extract.candidate import (
    CandidateEvent,
    dedupe,
    fragment,
    resolve_url,
)
from canarias_events.extract.dates_es import parse_date_range
from canarias_events.extract.util import as_number, lines_of, parse_html, text_of
from canarias_events.geo import normalize_municipality
from canarias_events.normalize import parse_datetime_any

EVENT_TYPES = frozenset(
    {
        "Event", "MusicEvent", "TheaterEvent", "Festival", "SportsEvent", "ExhibitionEvent",
        "SocialEvent", "EducationEvent", "ComedyEvent", "DanceEvent", "FoodEvent",
        "ChildrensEvent", "ScreeningEvent", "LiteraryEvent", "BusinessEvent", "VisualArtsEvent",
        "EventSeries", "CourseInstance", "DeliveryEvent", "Hackathon", "PublicationEvent",
        "SaleEvent", "UserInteraction",
    }
)  # fmt: skip

MAX_SCRIPT_CHARS = 2_000_000
MAX_DEPTH = 12
MAX_NODES = 20_000
MAX_CANDIDATES = 2000

_MIDNIGHT_NAIVE = re.compile(r"T00:00(?::00(?:\.0+)?)?$")
_HAS_ZONE = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")
_POSTAL = re.compile(r"\b(3[58]\d{3})\b")
_LEADING_BOLD = re.compile(
    r"^\s*<(strong|b)\b[^>]*>(?P<name>.*?)</\1>\s*(?:<br\s*/?>|</br>)?(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_STATUS = {
    "EventScheduled": "scheduled",
    "EventCancelled": "cancelled",
    "EventPostponed": "postponed",
    "EventRescheduled": "rescheduled",
    "EventMovedOnline": "moved_online",
}


@dataclass
class JsonLdResult:
    candidates: list[CandidateEvent] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    parse_errors: int = 0
    blocks: int = 0


# --------------------------------------------------------------------------- tolerant loading


def parse_jsonld_text(text: str) -> tuple[Any, str | None]:
    """Parse the text of a JSON-LD script; returns ``(data, None)`` or ``(None, reason)``."""
    if len(text) > MAX_SCRIPT_CHARS:
        return None, "oversized"
    cleaned = text.strip()
    attempts = [cleaned]
    stripped = re.sub(r"<!--|-->|//\s*<!\[CDATA\[|<!\[CDATA\[|//\s*\]\]>|\]\]>", "", cleaned)
    stripped = re.sub(r"(?m)^\s*//.*$", "", stripped)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL).strip()
    attempts.append(stripped)
    attempts.append(re.sub(r",(\s*[}\]])", r"\1", stripped))
    for attempt in attempts:
        if not attempt:
            continue
        try:
            return json.loads(attempt, strict=False), None
        except RecursionError:
            return None, "too deep"
        except ValueError:
            continue
    return None, "invalid"


# --------------------------------------------------------------------------- walking


def _types(node: dict[str, Any]) -> set[str]:
    raw = node.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    result: set[str] = set()
    for value in values:
        if isinstance(value, str):
            result.add(value.rsplit("/", 1)[-1].rsplit(":", 1)[-1])
    return result


@dataclass
class _Walk:
    found: list[dict[str, Any]] = field(default_factory=list)
    nodes: int = 0


def _walk(node: Any, depth: int, state: _Walk) -> None:
    if depth > MAX_DEPTH or state.nodes > MAX_NODES:
        return
    state.nodes += 1
    if isinstance(node, list):
        for item in node:
            _walk(item, depth + 1, state)
    elif isinstance(node, dict):
        if _types(node) & EVENT_TYPES:
            state.found.append(node)
            sub = node.get("subEvent")
            if sub is not None:
                _walk(sub, depth + 1, state)
            return
        for value in node.values():
            if isinstance(value, dict | list):
                _walk(value, depth + 1, state)


# --------------------------------------------------------------------------- mapping


def _moment(value: object) -> tuple[date | None, datetime | None] | None:
    """(date, None) for date-only values, (None, datetime) for instants, None when unreadable."""
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, dict):
        value = value.get("@value")
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    parsed = parse_datetime_any(raw)
    if parsed is None:
        spanish = parse_date_range(raw)
        if spanish is None:
            return None
        return spanish.start, None
    if parsed.all_day:
        return parsed.day, None
    if _MIDNIGHT_NAIVE.search(raw) and not _HAS_ZONE.search(raw):
        return parsed.day, None  # "T00:00" with no zone: the site publishes no time of day
    return None, parsed.value


def _first(value: object) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _as_list(value: object) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _name_of(value: object) -> str | None:
    value = _first(value)
    if isinstance(value, dict):
        return text_of(value.get("name"))
    return text_of(value)


def _url_of(base: str | None, value: object) -> str | None:
    value = _first(value)
    if isinstance(value, dict):
        value = value.get("url") or value.get("@id")
    return resolve_url(base, value)


def _image(base: str | None, value: object) -> str | None:
    for item in _as_list(value):
        if isinstance(item, dict):
            item = item.get("url") or item.get("contentUrl")
        url = resolve_url(base, item)
        if url:
            return url
    return None


def _map_location(obj: dict[str, Any], cand: CandidateEvent) -> None:
    places = [p for p in _as_list(obj.get("location")) if p]
    place: Any = None
    for item in places:
        kinds = _types(item) if isinstance(item, dict) else set()
        if "VirtualLocation" in kinds:
            cand.extra["online"] = True
            continue
        place = item
        break
    if place is None:
        return
    if isinstance(place, str):
        cand.venue_name = text_of(place)
        return
    if not isinstance(place, dict):
        return
    venue = text_of(place.get("name"))
    address = _first(place.get("address"))
    street: str | None = None
    locality: str | None = None
    postal: str | None = None
    if isinstance(address, str):
        raw_street = address
    elif isinstance(address, dict):
        street_value = address.get("streetAddress")
        raw_street = street_value if isinstance(street_value, str) else ""
        locality = text_of(address.get("addressLocality"))
        postal = text_of(address.get("postalCode"))
    else:
        raw_street = ""
    if raw_street:
        bold = _LEADING_BOLD.match(raw_street)
        if bold and not venue:
            venue = text_of(bold.group("name"))
            raw_street = bold.group("rest")
        street = ", ".join(lines_of(raw_street)) or None
    cand.venue_name = venue
    cand.address = street
    if postal is None and street:
        found = _POSTAL.search(street)
        postal = found.group(1) if found else None
    cand.postal_code = postal
    municipality = normalize_municipality(locality)
    if municipality is None and street:
        for part in reversed(street.split(",")):
            municipality = normalize_municipality(_POSTAL.sub("", part).strip())
            if municipality:
                break
    cand.municipality = municipality
    if locality and municipality is None:
        cand.extra["locality"] = locality
    geo = _first(place.get("geo"))
    if isinstance(geo, dict):
        cand.latitude = as_number(geo.get("latitude"))
        cand.longitude = as_number(geo.get("longitude"))


def _map_offers(obj: dict[str, Any], cand: CandidateEvent) -> None:
    free_flag = obj.get("isAccessibleForFree")
    free_flag = free_flag if isinstance(free_flag, bool) else None
    lows: list[float] = []
    highs: list[float] = []
    currency: str | None = None
    for offer in _as_list(obj.get("offers")):
        if not isinstance(offer, dict):
            continue
        offer_currency = text_of(offer.get("priceCurrency"))
        price = as_number(offer.get("price"))
        low = as_number(offer.get("lowPrice"))
        high = as_number(offer.get("highPrice"))
        # "0" with no currency is the CMS default, not a published price.
        placeholder = not offer_currency and free_flag is not True
        price = None if placeholder and price == 0 else price
        low = None if placeholder and low == 0 else low
        high = None if placeholder and high == 0 else high
        if price is not None:
            lows.append(price)
            highs.append(price)
        if low is not None:
            lows.append(low)
        if high is not None:
            highs.append(high)
        if offer_currency and currency is None and (price is not None or low or high):
            currency = offer_currency.upper()
        if cand.ticket_url is None:
            cand.ticket_url = resolve_url(None, offer.get("url"))
        availability = text_of(offer.get("availability"))
        if availability and "availability" not in cand.extra:
            cand.extra["availability"] = availability.rsplit("/", 1)[-1]
    if lows or highs:
        cand.price_min = min(lows) if lows else min(highs)
        cand.price_max = max(highs) if highs else max(lows)
        cand.currency = currency
        cand.is_free = bool(cand.price_max == 0)
    if free_flag is True:
        cand.is_free = True
        if cand.price_min is None:
            cand.price_min = cand.price_max = 0.0
    elif free_flag is False and cand.is_free is None:
        cand.is_free = False
    if free_flag is not None:
        cand.extra["is_accessible_for_free"] = free_flag


def candidate_from_schema(
    obj: dict[str, Any], page_url: str | None, *, strategy: str, url_fallback: bool = False
) -> tuple[CandidateEvent | None, str | None]:
    """Map one schema.org Event object; returns ``(candidate, None)`` or ``(None, reason)``."""
    name = _name_of(obj.get("name"))
    if not name:
        return None, "missing name"
    start = _moment(obj.get("startDate"))
    if start is None:
        return None, f"invalid startDate for {name!r}"
    start_day, start_dt = start
    end = _moment(obj.get("endDate"))
    cand = CandidateEvent(title=name, strategy=strategy, confidence="high")
    cand.start_date, cand.start_datetime = start_day, start_dt
    if end is not None:
        end_day, end_dt = end
        first = start_dt.date() if start_dt else start_day
        if end_dt is not None and start_dt is not None and end_dt >= start_dt:
            cand.end_datetime = end_dt
        elif first is not None:
            last = end_dt.date() if end_dt else end_day
            if last is not None and last > first:
                cand.end_date = last
            elif last == first and end_dt is not None:
                cand.end_datetime = end_dt if start_dt is None or end_dt >= start_dt else None
    description = text_of(obj.get("description"))
    cand.description = description
    kinds = sorted(_types(obj) & EVENT_TYPES)
    if kinds:
        cand.extra["schema_type"] = kinds[0]
    status = text_of(obj.get("eventStatus"))
    if status:
        cand.extra["status"] = _STATUS.get(status.rsplit("/", 1)[-1], status.rsplit("/", 1)[-1])
    language = obj.get("inLanguage")
    language = _first(language)
    if isinstance(language, dict):
        language = language.get("name") or language.get("alternateName")
    if isinstance(language, str) and language.strip():
        cand.extra["language"] = language.strip()
    age = text_of(obj.get("typicalAgeRange"))
    if age:
        cand.extra["age_range"] = age
    performers = [n for n in (_name_of(p) for p in _as_list(obj.get("performer"))) if n]
    if performers:
        cand.extra["performers"] = performers
    organizer = _first(obj.get("organizer"))
    cand.organizer_name = _name_of(organizer)
    cand.organizer_url = _url_of(page_url, organizer) if isinstance(organizer, dict) else None
    cand.image_url = _image(page_url, obj.get("image"))
    cand.official_event_url = _url_of(page_url, obj.get("url"))
    if cand.official_event_url is None and url_fallback:
        cand.official_event_url = resolve_url(None, page_url)
    _map_location(obj, cand)
    _map_offers(obj, cand)
    cand.raw = fragment(obj)
    return cand, None


# --------------------------------------------------------------------------- public API


def _collect(
    found: list[dict[str, Any]],
    page_url: str | None,
    strategy: str,
    url_fallback: bool,
    result: JsonLdResult,
) -> None:
    for obj in found:
        cand, reason = candidate_from_schema(
            obj, page_url, strategy=strategy, url_fallback=url_fallback
        )
        if cand is None:
            result.skipped.append(reason or "invalid")
        elif len(result.candidates) < MAX_CANDIDATES:
            result.candidates.append(cand)
    result.candidates = dedupe(result.candidates)


def extract_jsonld(
    html: str | bytes,
    page_url: str | None = None,
    *,
    url_fallback: bool = False,
    include_microdata: bool = False,
) -> JsonLdResult:
    """Every schema.org Event found in ``<script type="application/ld+json">`` blocks.

    ``url_fallback`` uses ``page_url`` as the event URL when the object has none (detail pages).
    """
    result = JsonLdResult()
    root = parse_html(html)
    if root is None:
        return result
    for script in root.xpath(
        '//script[translate(@type,"JSONLD+","jsonld+")="application/ld+json"]'
    ):
        result.blocks += 1
        data, error = parse_jsonld_text("".join(script.itertext()))
        if error is not None:
            result.parse_errors += 1
            continue
        state = _Walk()
        _walk(data, 0, state)
        _collect(state.found, page_url, "jsonld", url_fallback, result)
    if include_microdata:
        micro = extract_microdata(html, page_url, url_fallback=url_fallback)
        result.candidates = dedupe(result.candidates + micro.candidates)
        result.skipped.extend(micro.skipped)
    return result


# --------------------------------------------------------------------------- microdata

_MICRO_DEPTH = 8


def _micro_value(el: Any) -> str | None:
    tag = el.tag
    value: str | None
    if tag == "meta":
        value = el.get("content")
    elif tag in ("a", "link", "area"):
        value = el.get("href")
    elif tag in ("img", "source", "audio", "video", "embed", "iframe"):
        value = el.get("src")
    elif el.get("content"):
        value = el.get("content")
    elif tag == "time" and el.get("datetime"):
        value = el.get("datetime")
    else:
        value = " ".join(str(el.text_content()).split()) or None
    return str(value) if value else None


def _micro_scope(scope: Any, depth: int) -> dict[str, Any]:
    props: dict[str, Any] = {}
    itemtype = (scope.get("itemtype") or "").split()
    if itemtype:
        props["@type"] = itemtype[0].rstrip("/").rsplit("/", 1)[-1]

    def add(names: str, value: Any) -> None:
        for name in names.split():
            if name in props:
                existing = props[name]
                props[name] = (existing if isinstance(existing, list) else [existing]) + [value]
            else:
                props[name] = value

    def visit(node: Any, level: int) -> None:
        for child in node:
            if not isinstance(child.tag, str):
                continue
            prop = child.get("itemprop")
            if prop and child.get("itemscope") is not None:
                if level < _MICRO_DEPTH:
                    add(prop, _micro_scope(child, level + 1))
            elif prop:
                value = _micro_value(child)
                if value:
                    add(prop, value)
            elif child.get("itemscope") is None and level < _MICRO_DEPTH:
                visit(child, level + 1)

    visit(scope, depth)
    return props


def extract_microdata(
    html: str | bytes, page_url: str | None = None, *, url_fallback: bool = False
) -> JsonLdResult:
    """schema.org Events marked up with ``itemscope``/``itemtype``/``itemprop``."""
    result = JsonLdResult()
    root = parse_html(html)
    if root is None:
        return result
    found: list[dict[str, Any]] = []
    for scope in root.xpath("//*[@itemscope and contains(@itemtype, 'schema.org/')]")[:MAX_NODES]:
        kind = (scope.get("itemtype") or "").split()[0].rstrip("/").rsplit("/", 1)[-1]
        if kind in EVENT_TYPES:
            result.blocks += 1
            found.append(_micro_scope(scope, 0))
    _collect(found, page_url, "microdata", url_fallback, result)
    return result
