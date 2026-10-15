"""Adapter for The Events Calendar REST API (``/wp-json/tribe/events/v1/events``)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from canarias_events.date_range import TZ
from canarias_events.geo import normalize_municipality
from canarias_events.http import InvalidPayload, same_host_as
from canarias_events.normalize import html_to_text, parse_datetime_any, parse_price
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceFetchResult,
    clean_text,
    resolve_island,
)

DEFAULT_MAX_PAGES = 20
PER_PAGE = 50


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _first_dict(value: object) -> dict[str, Any]:
    """The Events Calendar sends ``[]`` when empty, a dict, or a list of dicts."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    return {}


def _names(value: object) -> list[str]:
    result: list[str] = []
    if isinstance(value, list):
        for item in value:
            name = clean_text(item.get("name")) if isinstance(item, dict) else None
            if name:
                result.append(html_to_text(name))
    return result


class TribeEventsAdapter(SourceAdapter):
    source_type = "tribe_events"

    def fetch(self, ctx: FetchContext) -> SourceFetchResult:
        config = self.config
        result = SourceFetchResult()
        host = urlsplit(config.url).hostname or ""
        max_pages = int(config.params.get("max_pages", DEFAULT_MAX_PAGES))
        url: str | None = config.url
        params: dict[str, Any] | None = {
            "start_date": ctx.window.start_date.isoformat(),
            "end_date": ctx.window.end_date_inclusive.isoformat(),
            "per_page": PER_PAGE,
        }
        while url is not None:
            payload = ctx.client.get_json(url, params=params, allowed_hosts={host})
            if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
                raise InvalidPayload(f"{config.id}: response has no 'events' list")
            result.pages += 1
            for item in payload["events"]:
                result.records_received += 1
                if isinstance(item, dict):
                    self._map(item, result)
                else:
                    result.discard("record is not an object", None)
            url, params = self._next(payload, result, max_pages), None
        return result

    def _next(
        self, payload: dict[str, Any], result: SourceFetchResult, max_pages: int
    ) -> str | None:
        next_url = payload.get("next_rest_url")
        if not isinstance(next_url, str) or not next_url:
            return None
        if not same_host_as(self.config.url, next_url):
            result.warnings.append(f"ignored next_rest_url on a different host: {next_url}")
            return None
        if result.pages >= max_pages:
            result.warnings.append(f"stopped at max_pages={max_pages}; more pages are available")
            return None
        return next_url

    # ------------------------------------------------------------------ mapping

    def _zone(self, item: dict[str, Any]) -> ZoneInfo:
        forced = self.config.params.get("force_timezone")
        declared = forced or item.get("timezone")
        if isinstance(declared, str) and "/" in declared:
            try:
                return ZoneInfo(declared)
            except (ZoneInfoNotFoundError, ValueError):
                pass
        # "UTC+0" style values are WordPress defaults, not zones: the site shows Canary wall time.
        return TZ

    def _map(self, item: dict[str, Any], result: SourceFetchResult) -> None:
        config = self.config
        source_id = clean_text(item.get("id"))
        title = html_to_text(clean_text(item.get("title")))
        if not title:
            result.discard("missing title", source_id)
            return
        zone = self._zone(item)
        start = parse_datetime_any(item.get("start_date"), zone)
        if start is None:
            result.discard("missing start date", source_id)
            return
        end = parse_datetime_any(item.get("end_date"), zone)
        all_day = bool(item.get("all_day"))
        start_dt: datetime | None = None
        end_dt: datetime | None = None
        start_day: date | None = None
        end_day: date | None = None
        if all_day or start.value is None:
            start_day = start.day
            end_day = end.day if end else None
        else:
            start_dt = start.value.astimezone(TZ)
            end_dt = end.value.astimezone(TZ) if end and end.value else None

        venue = _first_dict(item.get("venue"))
        latitude, longitude = _as_float(venue.get("geo_lat")), _as_float(venue.get("geo_lng"))
        city = clean_text(venue.get("city"))
        municipality = normalize_municipality(city) or normalize_municipality(config.municipality)
        organizer = _first_dict(item.get("organizer"))
        low, high, currency, is_free = self._price(item)
        categories = _names(item.get("categories"))
        tags = _names(item.get("tags"))
        image = item.get("image")
        extra: dict[str, Any] = {}
        if city and not normalize_municipality(city):
            extra["venue_city"] = city
        if clean_text(item.get("website")):
            extra["website"] = clean_text(item.get("website"))

        result.add_event(
            source_id,
            title=title,
            description=html_to_text(clean_text(item.get("description"))) or None,
            summary=html_to_text(clean_text(item.get("excerpt"))) or None,
            category=categories[0] if categories else None,
            tags=categories[1:] + tags,
            start_datetime=start_dt,
            end_datetime=end_dt,
            start_date=start_day,
            end_date=end_day,
            all_day=start_day is not None,
            venue_name=clean_text(venue.get("venue")),
            address=clean_text(venue.get("address")),
            municipality=municipality,
            island=resolve_island(
                config, municipality=municipality, latitude=latitude, longitude=longitude
            ),
            postal_code=clean_text(venue.get("zip")),
            latitude=latitude,
            longitude=longitude,
            price_min=low,
            price_max=high,
            currency=currency,
            is_free=is_free,
            official_event_url=clean_text(item.get("url")),
            image_url=image.get("url") if isinstance(image, dict) else None,
            organizer_name=clean_text(organizer.get("organizer")),
            organizer_url=clean_text(organizer.get("website")),
            contact_email=clean_text(organizer.get("email")),
            source_name=config.name,
            source_url=config.url,
            source_event_id=source_id,
            source_type=self.source_type,
            source_license=config.license,
            raw_data=item,
            extra=extra,
        )

    @staticmethod
    def _price(item: dict[str, Any]) -> tuple[float | None, float | None, str | None, bool | None]:
        low, high, currency, is_free = parse_price(clean_text(item.get("cost")))
        if low is not None or high is not None or is_free is not None:
            return low, high, currency, is_free
        details = item.get("cost_details")
        if not isinstance(details, dict) or not isinstance(details.get("values"), list):
            return None, None, None, None
        values = details["values"]
        symbol = clean_text(details.get("currency_symbol")) or ""
        parsed = [parse_price(f"{v} {symbol}".strip()) for v in values if clean_text(v)]
        mins = [p[0] for p in parsed if p[0] is not None]
        maxs = [p[1] for p in parsed if p[1] is not None]
        if not mins or not maxs:
            return None, None, None, None
        currency = next((p[2] for p in parsed if p[2]), None)
        return min(mins), max(maxs), currency, max(maxs) == 0
