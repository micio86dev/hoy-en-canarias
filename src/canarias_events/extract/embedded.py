"""Events embedded as JSON in the page: Next/Nuxt payloads, Livewire snapshots, data attributes.

Pages are parsed, never run: ``window.__NUXT__`` function payloads (JavaScript) are ignored.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

from canarias_events.date_range import TZ
from canarias_events.extract.candidate import CandidateEvent, dedupe, fragment, resolve_url
from canarias_events.extract.dates_es import YearPolicy, parse_date_range
from canarias_events.extract.util import as_number, parse_html, text_of
from canarias_events.normalize import html_to_text, parse_datetime_any, parse_price

MAX_BLOB_CHARS = 5_000_000
MAX_ATTR_CHARS = 300_000
MAX_DEPTH = 40
MAX_NODES = 300_000
MAX_CANDIDATES = 2000
MAX_ELEMENTS = 200_000
DEVALUE_DEPTH = 60

TITLE_KEYS = ("title", "name", "titulo", "nombre", "headline", "event_name")
START_KEYS = (
    "start_date", "startDate", "start", "fecha_inicio", "fecha", "date", "reserved_at",
    "starts_at", "start_at", "fecha_evento", "datetime", "dtstart", "date_start",
)  # fmt: skip
END_KEYS = ("end_date", "endDate", "end", "fecha_fin", "ends_at", "end_at", "date_end", "dtend")
START_TIME_KEYS = ("start_time", "hora_inicio", "hora", "time")
END_TIME_KEYS = ("end_time", "hora_fin")
URL_KEYS = ("public_url", "url", "link", "permalink", "href", "enlace")
TICKET_KEYS = ("tickets_url", "ticket_url", "booking_url", "url_entradas", "entradas_url")
IMAGE_KEYS = (
    "image",
    "image_url",
    "imagen",
    "main_image",
    "touch_image",
    "thumbnail",
    "cover_image",
)
VENUE_KEYS = ("space", "venue", "location", "lugar", "place", "recinto")
ROOM_KEYS = ("room", "sala")
CATEGORY_KEYS = ("category", "categoria", "genre")
DESCRIPTION_KEYS = ("description", "descripcion", "entry", "summary", "resumen")
PRICE_KEYS = ("price", "precio", "cost")

_DEVALUE_WRAPPERS = frozenset(
    {"ShallowReactive", "Reactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef", "readonly"}
)
_RE_NUXT_ASSIGN = re.compile(r"window\.__NUXT__\s*=\s*(\{.*\}|\[.*\])\s*;?\s*$", re.DOTALL)


@dataclass
class JsonBlob:
    source: str
    data: Any


@dataclass
class EmbeddedResult:
    candidates: list[CandidateEvent] = field(default_factory=list)
    blobs: int = 0
    parse_errors: int = 0
    skipped: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- Nuxt devalue


def resolve_devalue(payload: list[Any]) -> Any:
    """Resolve a Nuxt 3 ``__NUXT_DATA__`` array (devalue format) into plain Python objects.

    Pointers are indexes into the array; cycles are kept as cycles, bad pointers become None and
    nesting deeper than ``DEVALUE_DEPTH`` is truncated.
    """
    memo: dict[int, Any] = {}

    def resolve(index: Any, depth: int) -> Any:
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(payload):
            return None
        if index in memo:
            return memo[index]
        if depth > DEVALUE_DEPTH:
            return None
        value = payload[index]
        if isinstance(value, list):
            if value and isinstance(value[0], str):
                tag = value[0]
                if tag in _DEVALUE_WRAPPERS and len(value) > 1:
                    result = resolve(value[1], depth + 1)
                elif tag == "Date" and len(value) > 1:
                    result = value[1] if isinstance(value[1], str) else None
                else:
                    result = None
                memo[index] = result
                return result
            items: list[Any] = [None] * len(value)
            memo[index] = items
            for position, pointer in enumerate(value):
                items[position] = resolve(pointer, depth + 1)
            return items
        if isinstance(value, dict):
            obj: dict[str, Any] = {}
            memo[index] = obj
            for key, pointer in value.items():
                obj[key] = resolve(pointer, depth + 1)
            return obj
        memo[index] = value
        return value

    return resolve(0, 0)


# --------------------------------------------------------------------------- finding blobs


def _loads(text: str) -> tuple[Any, bool]:
    """(data, ok). Oversized or too deep payloads are not ok."""
    if len(text) > MAX_BLOB_CHARS:
        return None, False
    try:
        return json.loads(text, strict=False), True
    except (ValueError, RecursionError):
        return None, False


def _collect_blobs(root: Any) -> tuple[list[JsonBlob], int]:
    blobs: list[JsonBlob] = []
    errors = 0
    for script in root.iter("script"):
        kind = (script.get("type") or "").strip().lower()
        text = "".join(script.itertext()).strip()
        ident = script.get("id") or ""
        if kind == "application/ld+json" or not text:
            continue
        if (
            ident == "__NEXT_DATA__"
            or ident.endswith("NUXT_DATA__")
            or script.get("data-nuxt-data")
        ):
            data, ok = _loads(text)
            if not ok:
                errors += 1
            elif ident.endswith("NUXT_DATA__") or script.get("data-nuxt-data"):
                if isinstance(data, list):
                    blobs.append(JsonBlob("nuxt", resolve_devalue(data)))
            else:
                blobs.append(JsonBlob("next", data))
        elif kind == "application/json":
            data, ok = _loads(text)
            if ok:
                blobs.append(JsonBlob("json-script", data))
            else:
                errors += 1
        elif "window.__NUXT__" in text[:200]:
            match = _RE_NUXT_ASSIGN.search(text)
            if match:
                data, ok = _loads(match.group(1))
                if ok:
                    blobs.append(JsonBlob("window-nuxt", data))
    for index, element in enumerate(root.iter()):
        if index > MAX_ELEMENTS:
            break
        if not isinstance(element.tag, str):
            continue
        for name, value in element.attrib.items():
            if name in ("wire:snapshot", "wire:initial-data"):
                kind = name
            elif name.startswith("data-"):
                kind = "data-attr"
            else:
                continue
            stripped = value.lstrip()
            if len(value) > MAX_ATTR_CHARS or stripped[:1] not in ("{", "["):
                continue
            data, ok = _loads(value)
            if ok:
                blobs.append(JsonBlob(kind, data))
            elif kind != "data-attr":
                errors += 1
    return blobs, errors


def find_json_blobs(html: str | bytes) -> list[JsonBlob]:
    """Every JSON payload embedded in ``html`` (JSON-LD scripts excluded)."""
    root = parse_html(html)
    if root is None:
        return []
    return _collect_blobs(root)[0]


# --------------------------------------------------------------------------- walking


def _iter_dicts(data: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    stack: list[tuple[Any, int]] = [(data, 0)]
    nodes = 0
    while stack and nodes < MAX_NODES:
        node, depth = stack.pop()
        if not isinstance(node, dict | list) or id(node) in seen or depth > MAX_DEPTH:
            continue
        seen.add(id(node))
        nodes += 1
        children = list(node.values()) if isinstance(node, dict) else node
        if isinstance(node, dict):
            out.append(node)
        stack.extend(
            (child, depth + 1) for child in reversed(children) if isinstance(child, dict | list)
        )
    return out


# --------------------------------------------------------------------------- mapping


@dataclass
class _When:
    start_day: date | None = None
    start_dt: datetime | None = None
    end_day: date | None = None
    end_dt: datetime | None = None
    inferred: bool = False


def _scalar(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str | int):
        text = str(value).strip()
        return text or None
    return None


def _clock(value: object) -> time | None:
    text = _scalar(value)
    if text is None:
        return None
    try:
        return time.fromisoformat(text)
    except ValueError:
        match = re.fullmatch(r"(\d{1,2})[:.h](\d{2})", text)
        if match and int(match.group(1)) < 24 and int(match.group(2)) < 60:
            return time(int(match.group(1)), int(match.group(2)))
    return None


def _first_value(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _when_of(
    item: dict[str, Any], reference: date | None, policy: YearPolicy, context_year: int | None
) -> _When | None:
    start_clock = _clock(_first_value(item, START_TIME_KEYS))
    end_clock = _clock(_first_value(item, END_TIME_KEYS))
    when = _When()
    for key in START_KEYS:
        text = _scalar(item.get(key))
        if text is None:
            continue
        parsed = parse_datetime_any(text)
        if parsed is not None:
            if parsed.all_day:
                when.start_day = parsed.day
            else:
                when.start_dt = parsed.value
            break
        spanish = parse_date_range(
            text, reference_date=reference, year_policy=policy, context_year=context_year
        )
        if spanish is not None:
            when.start_day, when.end_day = spanish.start, spanish.end
            when.inferred = spanish.year_inferred
            start_clock = start_clock or spanish.start_time
            end_clock = end_clock or spanish.end_time
            break
    if when.start_day is None and when.start_dt is None:
        return None
    if when.start_day is not None and start_clock is not None:
        when.start_dt = datetime.combine(when.start_day, start_clock, TZ)
        when.start_day = None
    for key in END_KEYS:
        text = _scalar(item.get(key))
        if text is None:
            continue
        parsed = parse_datetime_any(text)
        if parsed is None:
            continue
        if parsed.all_day and end_clock is not None:
            when.end_dt = datetime.combine(parsed.day, end_clock, TZ)
        elif parsed.all_day:
            when.end_day = parsed.day
        else:
            when.end_dt = parsed.value
        break
    else:
        if end_clock is not None and when.start_dt is not None and when.end_day is None:
            same_day = datetime.combine(when.start_dt.date(), end_clock, TZ)
            when.end_dt = same_day if same_day >= when.start_dt else None
    _normalise_end(when)
    return when


def _normalise_end(when: _When) -> None:
    first = when.start_dt.date() if when.start_dt else when.start_day
    if when.end_dt is not None and when.start_dt is not None and when.end_dt < when.start_dt:
        when.end_dt = None
    if when.end_day is not None and first is not None and when.end_day <= first:
        when.end_day = None
    if when.end_dt is not None and when.start_dt is None and first is not None:
        # date-only start with a timed end: keep only the end day
        when.end_day = when.end_dt.date() if when.end_dt.date() > first else None
        when.end_dt = None


def _title_of(item: dict[str, Any]) -> str | None:
    for key in TITLE_KEYS:
        text = text_of(item.get(key))
        if text and 2 <= len(text) <= 300 and not text.replace(" ", "").isdigit():
            return text
    return None


def _name_of(value: object) -> str | None:
    if isinstance(value, dict):
        for key in ("name", "nombre", "title", "titulo"):
            text = text_of(value.get(key))
            if text:
                return text
        return None
    return text_of(value)


def _image_of(base: str | None, value: object) -> str | None:
    if isinstance(value, dict):
        for key in ("fullUrl", "url", "src", "path"):
            url = resolve_url(base, value.get(key))
            if url:
                return url
        return None
    return resolve_url(base, value)


def _flat(item: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in item.items():
        if isinstance(value, str):
            flat[key] = value[:300]
        elif isinstance(value, int | float | bool) or value is None:
            flat[key] = value
        elif isinstance(value, dict):
            name = _name_of(value)
            if name:
                flat[key] = name[:300]
    return flat


def _map_item(
    item: dict[str, Any],
    page_url: str | None,
    reference: date | None,
    policy: YearPolicy,
    context_year: int | None,
) -> CandidateEvent | None:
    if item.get("published") is False:
        return None
    title = _title_of(item)
    if title is None:
        return None
    when = _when_of(item, reference, policy, context_year)
    if when is None:
        return None
    url = None
    for key in URL_KEYS:
        url = resolve_url(page_url, item.get(key)) if isinstance(item.get(key), str) else None
        if url:
            break
    cand = CandidateEvent(
        title=title,
        start_date=when.start_day,
        start_datetime=when.start_dt,
        end_date=when.end_day,
        end_datetime=when.end_dt,
        official_event_url=url,
        strategy="embedded",
        confidence="high" if url else "medium",
        year_inferred=when.inferred,
    )
    description = _first_value(item, DESCRIPTION_KEYS)
    if isinstance(description, str):
        text = html_to_text(description)
        cand.description = text[:4000] or None
    cand.venue_name = _name_of(_first_value(item, VENUE_KEYS))
    room = _name_of(_first_value(item, ROOM_KEYS))
    if room:
        cand.extra["room"] = room
    cand.category = _name_of(_first_value(item, CATEGORY_KEYS))
    subtitle = text_of(item.get("subtitle"))
    if subtitle:
        cand.extra["subtitle"] = subtitle
    cancel = text_of(item.get("cancel_status"))
    if cancel:
        cand.extra["cancel_status"] = cancel
    for key in TICKET_KEYS:
        cand.ticket_url = (
            resolve_url(page_url, item.get(key)) if isinstance(item.get(key), str) else None
        )
        if cand.ticket_url:
            break
    image = _first_value(item, IMAGE_KEYS)
    cand.image_url = _image_of(page_url, image)
    price = _first_value(item, PRICE_KEYS)
    if isinstance(price, str):
        low, high, currency, free = parse_price(price)
        cand.price_min, cand.price_max, cand.currency, cand.is_free = low, high, currency, free
    elif isinstance(price, int | float) and not isinstance(price, bool):
        number = as_number(price)
        if number:
            cand.price_min = cand.price_max = number
    cand.raw = fragment(_flat(item))
    return cand


def extract_embedded(
    html: str | bytes,
    page_url: str | None = None,
    *,
    reference_date: date | None = None,
    year_policy: YearPolicy = "strict",
    context_year: int | None = None,
) -> EmbeddedResult:
    """Events found in embedded JSON: objects with a title-like and a parseable date-like key."""
    result = EmbeddedResult()
    root = parse_html(html)
    if root is None:
        return result
    blobs, result.parse_errors = _collect_blobs(root)
    result.blobs = len(blobs)
    found: list[CandidateEvent] = []
    for blob in blobs:
        for item in _iter_dicts(blob.data):
            cand = _map_item(item, page_url, reference_date, year_policy, context_year)
            if cand is not None:
                found.append(cand)
            if len(found) >= MAX_CANDIDATES:
                break
    result.candidates = dedupe(found)[:MAX_CANDIDATES]
    return result
