"""Adapter for iCalendar feeds (RFC 5545), expanding recurrences inside the window."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

from canarias_events.date_range import TZ, DateWindow
from canarias_events.geo import normalize_municipality
from canarias_events.http import InvalidPayload
from canarias_events.normalize import html_to_text
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceFetchResult,
    as_float,
    clean_text,
    municipality_in_text,
    resolve_island,
)

_RECURRENCE_PROPERTIES = ("RRULE", "RDATE", "RECURRENCE-ID")


def _text(component: Any, name: str) -> str | None:
    value = component.get(name)
    return clean_text(str(value)) if value is not None else None


def _categories(component: Any) -> list[str]:
    raw = component.get("CATEGORIES")
    groups = raw if isinstance(raw, list) else [raw]
    names: list[str] = []
    for group in groups:
        for cat in getattr(group, "cats", []) or []:
            name = clean_text(str(cat))
            if name:
                names.append(name)
    return names


def _image(component: Any) -> str | None:
    raw = component.get("ATTACH")
    for attachment in raw if isinstance(raw, list) else [raw]:
        if attachment is None:
            continue
        params = getattr(attachment, "params", {}) or {}
        if str(params.get("FMTTYPE", "")).startswith("image/"):
            return clean_text(str(attachment))
    return None


class IcalAdapter(SourceAdapter):
    source_type = "ical"

    def fetch(self, ctx: FetchContext) -> SourceFetchResult:
        config = self.config
        return self.read(ctx.client.get(config.url).content, ctx.window)

    def read(self, content: bytes, window: DateWindow) -> SourceFetchResult:
        """Events of an iCalendar document, recurrences expanded inside ``window``."""
        config = self.config
        result = SourceFetchResult()
        try:
            calendar = icalendar.Calendar.from_ical(content)
        except ValueError as exc:
            raise InvalidPayload(f"{config.id}: response is not an iCalendar") from exc
        if not isinstance(calendar, icalendar.Calendar):
            raise InvalidPayload(f"{config.id}: response is not an iCalendar")
        result.pages = 1

        recurring: dict[str, bool] = {}
        undated = []
        for component in calendar.walk("VEVENT"):
            result.records_received += 1
            uid = _text(component, "UID")
            if "DTSTART" not in component:
                result.discard("missing start date", uid)
                undated.append(component)  # the expander cannot handle events without DTSTART
            elif uid:
                recurring[uid] = recurring.get(uid, False) or any(
                    name in component for name in _RECURRENCE_PROPERTIES
                )

        calendar.subcomponents = [
            c for c in calendar.subcomponents if not any(c is u for u in undated)
        ]
        reported: set[str] = set()
        occurrences = recurring_ical_events.of(calendar).between(window.start, window.end)
        for component in occurrences:
            self._map(component, recurring, reported, result)
        return result

    # ------------------------------------------------------------------ mapping

    def _zone(self) -> ZoneInfo | None:
        forced = self.config.params.get("force_timezone")
        return ZoneInfo(str(forced)) if forced else None

    def _localize(self, value: datetime) -> datetime:
        forced = self._zone()
        if forced is not None:
            return value.replace(tzinfo=forced).astimezone(TZ)
        if value.tzinfo is None:
            return value.replace(tzinfo=TZ)
        return value.astimezone(TZ)

    def _map(
        self,
        component: Any,
        recurring: dict[str, bool],
        reported: set[str],
        result: SourceFetchResult,
    ) -> None:
        config = self.config
        uid = _text(component, "UID")
        title = html_to_text(_text(component, "SUMMARY"))
        reason = None
        if (_text(component, "STATUS") or "").upper() == "CANCELLED":
            reason = "cancelled"
        elif not title:
            reason = "missing title"
        if reason is not None:
            if uid not in reported:
                reported.add(uid or "")
                result.discard(reason, uid)
            return

        start, end = component.start, component.end
        start_dt: datetime | None = None
        end_dt: datetime | None = None
        start_day: date | None = None
        end_day: date | None = None
        if isinstance(start, datetime):
            start_dt = self._localize(start)
            end_dt = self._localize(end) if isinstance(end, datetime) else None
            stamp = start_dt.isoformat()
        else:
            start_day = start
            if isinstance(end, date) and not isinstance(end, datetime):
                last = end - timedelta(days=1)  # DTEND;VALUE=DATE is exclusive
                end_day = last if last > start else None
            stamp = start.isoformat()

        location = _text(component, "LOCATION")
        geo = component.get("GEO")
        latitude = as_float(getattr(geo, "latitude", None))
        longitude = as_float(getattr(geo, "longitude", None))
        municipality = municipality_in_text(location) or normalize_municipality(config.municipality)
        categories = _categories(component)
        source_id = f"{uid}#{stamp}" if uid and recurring.get(uid) else uid

        result.add_event(
            source_id,
            title=title,
            description=html_to_text(_text(component, "DESCRIPTION")) or None,
            category=categories[0] if categories else None,
            tags=categories[1:],
            start_datetime=start_dt,
            end_datetime=end_dt,
            start_date=start_day,
            end_date=end_day,
            all_day=start_day is not None,
            venue_name=location,
            municipality=municipality,
            island=resolve_island(
                config, municipality=municipality, latitude=latitude, longitude=longitude
            ),
            latitude=latitude,
            longitude=longitude,
            official_event_url=_text(component, "URL"),
            image_url=_image(component),
            source_name=config.name,
            source_url=config.url,
            source_event_id=source_id,
            source_type=self.source_type,
            source_license=config.license,
            raw_data=self._raw(component),
        )

    @staticmethod
    def _raw(component: Any) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        for key, value in component.items():
            items = value if isinstance(value, list) else [value]
            texts = [str(getattr(item, "dt", item)) for item in items]
            raw[str(key)] = texts[0] if len(texts) == 1 else texts
        return raw
