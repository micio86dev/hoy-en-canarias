"""Adapter for RSS and Atom feeds that carry structured event dates.

Only items exposing a machine-readable event date are accepted (RSS event module
``ev:startdate``/``ev:enddate``, ``xcal:dtstart``/``dtend`` or a field named in
``params.date_field``/``params.end_field``). Publication dates are not event dates and
dates are never parsed out of titles or descriptions.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import feedparser

from canarias_events.date_range import TZ
from canarias_events.geo import normalize_municipality
from canarias_events.http import InvalidPayload
from canarias_events.normalize import ParsedMoment, html_to_text, parse_datetime_any
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceFetchResult,
    clean_text,
    municipality_in_text,
    resolve_island,
)

START_FIELDS = ("ev_startdate", "xcal_dtstart")
END_FIELDS = ("ev_enddate", "xcal_dtend")


class RssAdapter(SourceAdapter):
    source_type = "rss"

    def fetch(self, ctx: FetchContext) -> SourceFetchResult:
        return self.read(ctx.client.get(self.config.url).content)

    def read(self, content: bytes) -> SourceFetchResult:
        """Events of an RSS/Atom document (only items with a structured event date)."""
        config = self.config
        result = SourceFetchResult()
        parsed = feedparser.parse(content)
        if not parsed.entries and parsed.bozo and not parsed.feed.get("title"):
            raise InvalidPayload(f"{config.id}: response is not an RSS/Atom feed")
        result.pages = 1
        start_fields = (
            (config.params["date_field"],) if "date_field" in config.params else START_FIELDS
        )
        end_fields = (config.params["end_field"],) if "end_field" in config.params else END_FIELDS
        for entry in parsed.entries:
            result.records_received += 1
            self._map(entry, start_fields, end_fields, result)
        return result

    @staticmethod
    def _first_moment(entry: Any, fields: tuple[str, ...]) -> ParsedMoment | None:
        for name in fields:
            moment = parse_datetime_any(entry.get(name))
            if moment is not None:
                return moment
        return None

    def _map(
        self,
        entry: Any,
        start_fields: tuple[str, ...],
        end_fields: tuple[str, ...],
        result: SourceFetchResult,
    ) -> None:
        config = self.config
        link = clean_text(entry.get("link"))
        source_id = clean_text(entry.get("id")) or link
        title = html_to_text(clean_text(entry.get("title")))
        if not title:
            result.discard("missing title", source_id)
            return
        start = self._first_moment(entry, start_fields)
        if start is None:
            result.discard("no structured event date", source_id)
            return
        end = self._first_moment(entry, end_fields)

        start_dt: datetime | None = start.value.astimezone(TZ) if start.value else None
        start_day: date | None = None if start_dt else start.day
        end_dt = end.value.astimezone(TZ) if end and end.value and start_dt else None
        end_day = end.day if end and start_day is not None else None

        summary = entry.get("summary")
        if not summary and entry.get("content"):
            summary = entry["content"][0].get("value")
        tags = [t["term"] for t in entry.get("tags", []) if clean_text(t.get("term"))]
        location = clean_text(entry.get("ev_location"))
        municipality = municipality_in_text(location) or normalize_municipality(config.municipality)

        result.add_event(
            source_id,
            title=title,
            description=html_to_text(clean_text(summary)) or None,
            category=tags[0] if tags else None,
            tags=tags[1:],
            start_datetime=start_dt,
            end_datetime=end_dt,
            start_date=start_day,
            end_date=end_day,
            all_day=start_day is not None,
            venue_name=location,
            municipality=municipality,
            island=resolve_island(config, municipality=municipality),
            official_event_url=link,
            source_name=config.name,
            source_url=config.url,
            source_event_id=source_id,
            source_type=self.source_type,
            source_license=config.license,
            raw_data=json.loads(json.dumps(dict(entry), default=str)),
        )
