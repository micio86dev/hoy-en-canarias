"""Adapter for the Ticketmaster Discovery API v2 (opt-in: needs a free API key).

The key is read from the environment variable named by ``config.auth_env`` and is only ever
sent as a query parameter through :class:`SafeHttpClient`, which masks it in logs and errors.
Without a key the adapter returns a warning and makes no request.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from canarias_events.date_range import TZ
from canarias_events.geo import ISLANDS, normalize_municipality
from canarias_events.normalize import parse_datetime_any
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceFetchResult,
    as_float,
    clean_text,
    resolve_island,
)

PAGE_SIZE = 100
MAX_ITEMS = 1000  # Discovery API deep-paging limit: size * page must stay below 1000
GEOHASH_PRECISION = 5
_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"

# island -> (centroid latitude, centroid longitude, search radius in km); overridable in params.
DEFAULT_ISLAND_POINTS: dict[str, tuple[float, float, float]] = {
    "Tenerife": (28.29, -16.55, 45),
    "Gran Canaria": (27.96, -15.59, 40),
    "Lanzarote": (29.04, -13.63, 35),
    "Fuerteventura": (28.36, -14.01, 60),
    "La Palma": (28.68, -17.86, 25),
    "La Gomera": (28.11, -17.22, 20),
    "El Hierro": (27.74, -18.02, 20),
    "La Graciosa": (29.23, -13.50, 8),
}


def geohash_encode(latitude: float, longitude: float, precision: int = GEOHASH_PRECISION) -> str:
    """Standard geohash (base32, interleaved longitude/latitude bisection)."""
    lat_range, lon_range = [-90.0, 90.0], [-180.0, 180.0]
    chars: list[str] = []
    bits, value, even = 0, 0, True
    while len(chars) < precision:
        rng, coord = (lon_range, longitude) if even else (lat_range, latitude)
        mid = (rng[0] + rng[1]) / 2
        if coord >= mid:
            value = (value << 1) | 1
            rng[0] = mid
        else:
            value <<= 1
            rng[1] = mid
        even = not even
        bits += 1
        if bits == 5:
            chars.append(_BASE32[value])
            bits, value = 0, 0
    return "".join(chars)


def _utc_stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class TicketmasterAdapter(SourceAdapter):
    source_type = "ticketmaster"

    def fetch(self, ctx: FetchContext) -> SourceFetchResult:
        config = self.config
        result = SourceFetchResult()
        key = (ctx.env.get(config.auth_env) or "").strip() if config.auth_env else ""
        if not key:
            hint = f" (set {config.auth_env})" if config.auth_env else ""
            result.warnings.append(f"skipped: missing API key{hint}")
            return result
        host = {urlsplit(config.url).hostname or ""}
        seen: set[str] = set()
        for island in config.islands or list(ISLANDS):
            point = self._point(island)
            if point is None:
                continue
            lat, lon, radius = point
            base: dict[str, Any] = {
                "apikey": key,
                "countryCode": "ES",
                "geoPoint": geohash_encode(lat, lon, GEOHASH_PRECISION),
                "radius": radius,
                "unit": "km",
                "startDateTime": _utc_stamp(ctx.window.start),
                "endDateTime": _utc_stamp(ctx.window.end),
                "size": PAGE_SIZE,
                "sort": "date,asc",
            }
            self._fetch_island(ctx, island, base, host, seen, result)
        return result

    def _point(self, island: str) -> tuple[float, float, float] | None:
        override = self.config.params.get("island_points", {}).get(island)
        if isinstance(override, dict):
            return float(override["lat"]), float(override["lon"]), override["radius"]
        return DEFAULT_ISLAND_POINTS.get(island)

    def _fetch_island(
        self,
        ctx: FetchContext,
        island: str,
        base: dict[str, Any],
        host: set[str],
        seen: set[str],
        result: SourceFetchResult,
    ) -> None:
        page = 0
        while True:
            payload = ctx.client.get_json(
                self.config.url, params={**base, "page": page}, allowed_hosts=host
            )
            result.pages += 1
            embedded = payload.get("_embedded") if isinstance(payload, dict) else None
            events = embedded.get("events") if isinstance(embedded, dict) else None
            for item in events if isinstance(events, list) else []:
                result.records_received += 1
                if isinstance(item, dict):
                    self._map(item, seen, result)
                else:
                    result.discard("record is not an object", None)
            info = payload.get("page", {}) if isinstance(payload, dict) else {}
            total_pages = int(info.get("totalPages") or 0)
            size = int(info.get("size") or PAGE_SIZE)
            page += 1
            if page >= total_pages:
                return
            if page * size + size > MAX_ITEMS:
                result.warnings.append(
                    f"{island}: results limited to the first {MAX_ITEMS} "
                    f"(totalElements={info.get('totalElements')})"
                )
                return

    # ------------------------------------------------------------------ mapping

    @staticmethod
    def _moment(part: object, zone: ZoneInfo) -> tuple[datetime | None, date | None]:
        """(aware datetime, None) or (None, date) from a Discovery ``dates.start/end`` object."""
        if not isinstance(part, dict):
            return None, None
        instant = parse_datetime_any(part.get("dateTime"))
        if instant is not None and instant.value is not None:
            return instant.value.astimezone(TZ), None
        local_date = clean_text(part.get("localDate"))
        local_time = clean_text(part.get("localTime"))
        if local_date and local_time:
            moment = parse_datetime_any(f"{local_date}T{local_time}", zone)
            if moment is not None and moment.value is not None:
                return moment.value.astimezone(TZ), None
        if local_date:
            day = parse_datetime_any(local_date)
            return None, day.day if day else None
        return None, None

    def _map(self, item: dict[str, Any], seen: set[str], result: SourceFetchResult) -> None:
        config = self.config
        source_id = clean_text(item.get("id"))
        title = clean_text(item.get("name"))
        if not title:
            result.discard("missing title", source_id)
            return
        dates = self._dict(item.get("dates"))
        if str(self._dict(dates.get("status")).get("code", "")).lower() == "cancelled":
            result.discard("cancelled", source_id)
            return
        venues = self._dict(item.get("_embedded")).get("venues")
        venue = self._dict(venues[0]) if isinstance(venues, list) and venues else {}
        zone = self._zone(dates.get("timezone") or venue.get("timezone"))
        start_dt, start_day = self._moment(dates.get("start"), zone)
        if start_dt is None and start_day is None:
            result.discard("missing start date", source_id)
            return
        end_dt, end_day = self._moment(dates.get("end"), zone)
        if source_id:
            if source_id in seen:
                result.discard("duplicate event id", source_id)
                return
            seen.add(source_id)

        location = self._dict(venue.get("location"))
        latitude, longitude = (
            as_float(location.get("latitude")),
            as_float(location.get("longitude")),
        )
        city = self._name(venue.get("city"))
        municipality = normalize_municipality(city)
        address = venue.get("address")
        low, high, currency = self._price(item.get("priceRanges"))
        category, tags = self._classification(item.get("classifications"))
        url = clean_text(item.get("url"))
        extra: dict[str, Any] = {}
        if city and not municipality:
            extra["venue_city"] = city

        result.add_event(
            source_id,
            title=title,
            category=category,
            tags=tags,
            start_datetime=start_dt,
            end_datetime=end_dt,
            start_date=start_day if start_dt is None else None,
            end_date=end_day if start_dt is None else None,
            all_day=start_dt is None,
            venue_name=clean_text(venue.get("name")),
            address=clean_text(address.get("line1")) if isinstance(address, dict) else None,
            municipality=municipality,
            island=resolve_island(
                config, municipality=municipality, latitude=latitude, longitude=longitude
            ),
            postal_code=clean_text(venue.get("postalCode")),
            latitude=latitude,
            longitude=longitude,
            price_min=low,
            price_max=high,
            currency=currency,
            is_free=None if low is None else (high == 0),
            ticket_url=url,
            official_event_url=url,
            image_url=self._image(item.get("images")),
            source_name=config.name,
            source_url=config.url,
            source_event_id=source_id,
            source_type=self.source_type,
            source_license=config.license,
            raw_data=item,
            extra=extra,
        )

    @staticmethod
    def _dict(value: object) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _zone(name: object) -> ZoneInfo:
        if isinstance(name, str) and name.strip():
            try:
                return ZoneInfo(name.strip())
            except (ZoneInfoNotFoundError, ValueError):
                pass
        return TZ

    @staticmethod
    def _name(value: object) -> str | None:
        return clean_text(value.get("name")) if isinstance(value, dict) else None

    @staticmethod
    def _price(ranges: object) -> tuple[float | None, float | None, str | None]:
        mins: list[float] = []
        maxs: list[float] = []
        currency: str | None = None
        for entry in ranges if isinstance(ranges, list) else []:
            if not isinstance(entry, dict):
                continue
            low, high = as_float(entry.get("min")), as_float(entry.get("max"))
            if low is not None:
                mins.append(low)
            if high is not None:
                maxs.append(high)
            currency = currency or clean_text(entry.get("currency"))
        if not mins and not maxs:
            return None, None, None
        return (min(mins) if mins else None, max(maxs) if maxs else None, currency)

    @staticmethod
    def _classification(items: object) -> tuple[str | None, list[str]]:
        for entry in items if isinstance(items, list) else []:
            if not isinstance(entry, dict):
                continue
            names = [
                n
                for n in (
                    TicketmasterAdapter._name(entry.get("segment")),
                    TicketmasterAdapter._name(entry.get("genre")),
                    TicketmasterAdapter._name(entry.get("subGenre")),
                )
                if n and n.lower() != "undefined"
            ]
            if names:
                return names[0], names[1:]
        return None, []

    @staticmethod
    def _image(images: object) -> str | None:
        best: tuple[float, str] | None = None
        for image in images if isinstance(images, list) else []:
            if isinstance(image, dict) and clean_text(image.get("url")):
                width = as_float(image.get("width")) or 0.0
                if best is None or width > best[0]:
                    best = (width, str(image["url"]))
        return best[1] if best else None
