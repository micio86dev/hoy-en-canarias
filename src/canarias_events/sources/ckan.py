"""Adapter for CKAN open-data portals (``package_show`` plus a JSON or CSV resource).

Field names differ per dataset, so the mapping is a dict of canonical key -> source column.
Two profiles ship with defaults (``tenerife_sports`` and ``gobcan_agenda``); ``params.field_map``
overrides individual keys. Canonical keys: id, title, url, description, venue, address,
municipality, island, organizer, category, start, end, hour, minute, image.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import UTC, date, datetime, time
from typing import Any
from urllib.parse import urlsplit

from canarias_events.date_range import TZ
from canarias_events.geo import normalize_municipality
from canarias_events.http import InvalidPayload
from canarias_events.normalize import ParsedMoment, html_to_text, parse_datetime_any
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceFetchResult,
    clean_text,
    resolve_island,
)

DEFAULT_STALE_DAYS = 180
PROFILES: dict[str, dict[str, str]] = {
    "tenerife_sports": {
        "title": "evento_nombre",
        "url": "evento_url",
        "description": "evento_descripcion",
        "venue": "evento_lugar",
        "municipality": "municipio_nombre",
        "organizer": "evento_organizador",
        "start": "evento_fecha_inicio",
        "end": "evento_fecha_fin",
    },
    "gobcan_agenda": {
        "start": "FECHA_INICIO",
        "end": "FECHA_FIN",
        "category": "TIPO_EVENTO",
        "island": "ISLA",
        "municipality": "MUNICIPIO",
        "venue": "DENOMINACION_ESPACIO",
        "address": "LUGAR",
        "title": "TITULO",
        "description": "DESCRIPCION",
        "hour": "HORA",
        "minute": "MINUTO",
        "url": "MAS_INFO",
        "image": "URL_IMAGEN",
    },
}
_DICTIONARY = re.compile(r"diccionario|dictionary", re.IGNORECASE)
_NUMERIC_ID = re.compile(r"\d+(?:\.0+)?")
_NATURAL_KEY = ("title", "url", "start", "end", "venue", "address", "municipality")


class CkanAdapter(SourceAdapter):
    source_type = "ckan"

    def fetch(self, ctx: FetchContext) -> SourceFetchResult:
        config = self.config
        result = SourceFetchResult()
        host = {urlsplit(config.url).hostname or ""}
        package = ctx.client.get_json(config.url, allowed_hosts=host)
        if not isinstance(package, dict) or package.get("success") is not True:
            raise InvalidPayload(f"{config.id}: CKAN package_show was not successful")
        info = package.get("result")
        if not isinstance(info, dict):
            raise InvalidPayload(f"{config.id}: CKAN response has no 'result'")
        resource = self._select_resource(info)
        self._check_stale(info, ctx, result)

        resource_url = resource.get("url")
        if not isinstance(resource_url, str):
            raise InvalidPayload(f"{config.id}: selected resource has no url")
        response = ctx.client.get(resource_url, allowed_hosts=host)
        result.pages = 1
        fmt = str(resource.get("format", "")).upper()
        records = (
            self._csv_records(response.content)
            if fmt == "CSV"
            else self._json_records(response.content)
        )

        field_map = {**PROFILES[self._profile()], **config.params.get("field_map", {})}
        seen: set[str] = set()
        for record in records:
            result.records_received += 1
            if isinstance(record, dict):
                self._map(record, field_map, result, seen)
            else:
                result.discard("record is not an object", None)
        return result

    # ------------------------------------------------------------------ package

    def _profile(self) -> str:
        profile = str(self.config.params.get("profile", "tenerife_sports"))
        if profile not in PROFILES:
            raise ValueError(f"{self.config.id}: unknown CKAN profile '{profile}'")
        return profile

    def _select_resource(self, info: dict[str, Any]) -> dict[str, Any]:
        params = self.config.params
        resources = [r for r in info.get("resources", []) if isinstance(r, dict)]
        wanted_id = params.get("resource_id")
        fmt = str(params.get("resource_format", "JSON")).upper()
        for resource in resources:
            if wanted_id:
                if resource.get("id") == wanted_id:
                    return resource
            elif str(resource.get("format", "")).upper() == fmt and not _DICTIONARY.search(
                str(resource.get("name", ""))
            ):
                return resource
        what = f"resource {wanted_id}" if wanted_id else f"{fmt} resource"
        raise InvalidPayload(f"{self.config.id}: no {what} found in package")

    def _check_stale(
        self, info: dict[str, Any], ctx: FetchContext, result: SourceFetchResult
    ) -> None:
        modified = parse_datetime_any(info.get("metadata_modified"), UTC)
        if modified is None or modified.value is None:
            return
        limit = int(self.config.params.get("stale_days", DEFAULT_STALE_DAYS))
        age = (ctx.now - modified.value).days
        if age > limit:
            result.warnings.append(
                f"stale: dataset metadata_modified {modified.value.date()} is {age} days old "
                f"(limit {limit})"
            )

    # ------------------------------------------------------------------ records

    def _json_records(self, content: bytes) -> list[Any]:
        try:
            payload = json.loads(content)
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidPayload(f"{self.config.id}: resource is not valid JSON") from exc
        if isinstance(payload, list):
            return payload
        key = str(self.config.params.get("records_key", "eventos"))
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return list(payload[key])
        raise InvalidPayload(f"{self.config.id}: JSON resource has no '{key}' list")

    def _csv_records(self, content: bytes) -> list[Any]:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        sample = text[:4096]
        try:
            delimiter = csv.Sniffer().sniff(sample.split("\n", 1)[0], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
        return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))

    # ------------------------------------------------------------------ mapping

    def _map(
        self,
        record: dict[str, Any],
        field_map: dict[str, str],
        result: SourceFetchResult,
        seen: set[str],
    ) -> None:
        config = self.config

        def value(key: str) -> str | None:
            column = field_map.get(key)
            return clean_text(record.get(column)) if column else None

        title = html_to_text(value("title"))
        url = value("url")
        identifier = title or url
        if not title:
            result.discard("missing title", identifier)
            return
        start = parse_datetime_any(value("start"))
        if start is None:
            result.discard("missing start date", identifier)
            return
        end = parse_datetime_any(value("end"))
        start_dt, start_day = self._with_time(start, value("hour"), value("minute"))
        end_dt = end.value if end and end.value else None
        end_day = end.day if end and end.value is None else None

        source_id = value("id") or self._natural_id(
            {key: value(key) for key in _NATURAL_KEY} | {"start": str(value("start"))}
        )
        if source_id in seen:
            result.discard("duplicate event id", source_id)
            return
        seen.add(source_id)

        municipality_raw = value("municipality")
        municipality = normalize_municipality(municipality_raw)
        organizer = value("organizer")
        extra: dict[str, Any] = {}
        if municipality_raw and not municipality:
            extra["municipality_raw"] = municipality_raw
        result.add_event(
            source_id,
            title=title,
            description=html_to_text(value("description")) or None,
            category=value("category"),
            start_datetime=start_dt,
            end_datetime=end_dt,
            start_date=start_day,
            end_date=end_day,
            all_day=start_day is not None,
            venue_name=value("venue"),
            address=value("address"),
            municipality=municipality,
            island=resolve_island(config, island=value("island"), municipality=municipality),
            organizer_name=None if organizer and _NUMERIC_ID.fullmatch(organizer) else organizer,
            official_event_url=url,
            image_url=value("image"),
            source_name=config.name,
            source_url=config.url,
            source_event_id=source_id,
            source_type=self.source_type,
            source_license=config.license,
            raw_data=record,
            extra=extra,
        )

    @staticmethod
    def _with_time(
        start: ParsedMoment, hour: str | None, minute: str | None
    ) -> tuple[datetime | None, date | None]:
        if start.value is not None:
            return start.value.astimezone(TZ), None
        if hour is not None and hour.isdigit() and int(hour) < 24:
            minutes = (
                int(minute) if minute is not None and minute.isdigit() and int(minute) < 60 else 0
            )
            return datetime.combine(start.day, time(int(hour), minutes), tzinfo=TZ), None
        return None, start.day

    @staticmethod
    def _natural_id(parts: dict[str, str | None]) -> str:
        blob = "\x1f".join(f"{key}={parts[key] or ''}" for key in sorted(parts))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
