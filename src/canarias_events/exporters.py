"""Write the JSON/CSV/GeoJSON event exports and the import and sources reports, atomically."""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import tempfile
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from canarias_events.database import Database
from canarias_events.date_range import DateWindow
from canarias_events.geo_points import MapPoint, Venue, load_venues, locate
from canarias_events.health import summarize
from canarias_events.models import Event
from canarias_events.pipeline import events_in_window
from canarias_events.sources.base import SourceConfig
from canarias_events.sources.registry import SourcesCatalog

CSV_BOM = b"\xef\xbb\xbf"
_NOT_IN_CSV = frozenset({"raw_data", "extra", "tags"})
_CSV_COLUMNS = [
    name
    for name in Event.model_fields
    if name not in _NOT_IN_CSV and not name.startswith("source_")
]
_CSV_EXTRA = ["tags", "sources", "source_urls", "source_event_ids", "source_license"]
_CSV_MAP = ["map_latitude", "map_longitude", "map_precision"]
logger = logging.getLogger("canarias_events.exporters")

DEFAULT_VENUES = Path("config/venues.yaml")
_GEOJSON_PROPERTIES = [
    "id", "title", "start_date", "start_datetime", "end_date", "end_datetime", "venue_name",
    "municipality", "island", "official_event_url", "source_name",
]  # fmt: skip


def write_atomic(path: Path, data: bytes) -> None:
    """Write via a temp file in the same directory, then replace: readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _json_bytes(payload: Any) -> bytes:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return (text + "\n").encode("utf-8")


# --------------------------------------------------------------------------- events


def _source_links(db: Database, event: Event, *, raw: bool) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for row in db.event_sources(event.id or ""):
        link: dict[str, Any] = {
            "source_id": row["source_id"],
            "source_name": row["source_name"],
            "source_url": row["source_url"],
            "source_event_id": row["source_event_id"],
            "official_event_url": row["official_event_url"],
            "match_method": row["match_method"],
        }
        if raw:
            link["raw_data"] = json.loads(row["raw_data"])
        links.append(link)
    return links


def events_payload(
    db: Database, events: list[Event], venues: list[Venue] | None = None
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for event in events:
        item = event.model_dump(mode="json", exclude={"raw_data"})
        item["sources"] = _source_links(db, event, raw=True)
        point = locate(event, venues or [])
        item["map"] = (
            {
                "latitude": point.latitude,
                "longitude": point.longitude,
                "precision": point.precision,
                "basis": point.basis,
            }
            if point
            else None
        )
        payload.append(item)
    return payload


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def events_csv(db: Database, events: list[Event], venues: list[Venue] | None = None) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow([*_CSV_COLUMNS, *_CSV_EXTRA, *_CSV_MAP])
    for event in events:
        links = _source_links(db, event, raw=False)
        row = [_cell(getattr(event, name)) for name in _CSV_COLUMNS]
        row += [
            "|".join(event.tags),
            "|".join(str(link["source_name"]) for link in links),
            "|".join(str(link["source_url"] or "") for link in links),
            "|".join(str(link["source_event_id"]) for link in links),
            _cell(event.source_license),
        ]
        point = locate(event, venues or [])
        row += [
            _cell(point.latitude if point else None),
            _cell(point.longitude if point else None),
            _cell(point.precision if point else None),
        ]
        writer.writerow(row)
    return CSV_BOM + buffer.getvalue().encode("utf-8")


def events_geojson(events: list[Event], venues: list[Venue] | None = None) -> dict[str, Any]:
    """A FeatureCollection with one Point (lon, lat) per event that has a map point."""
    features: list[dict[str, Any]] = []
    for event in events:
        point: MapPoint | None = locate(event, venues or [])
        if point is None:
            continue
        properties = event.model_dump(mode="json", include=set(_GEOJSON_PROPERTIES))
        properties = {name: properties.get(name) for name in _GEOJSON_PROPERTIES}
        properties["precision"] = point.precision
        properties["basis"] = point.basis
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [point.longitude, point.latitude]},
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}


# --------------------------------------------------------------------------- sources report


def source_status(cfg: SourceConfig, env: Mapping[str, str]) -> str:
    if cfg.stale:
        return "stale"
    if not cfg.enabled:
        return "disabled"
    if cfg.auth_env and not (env.get(cfg.auth_env) or "").strip():
        return "skipped"
    return "integrated"


def build_sources_report(
    catalog: SourcesCatalog, db: Database, now: datetime, env: Mapping[str, str]
) -> dict[str, Any]:
    stored = db.source_rows()
    entries: list[dict[str, Any]] = []
    for cfg in catalog.sources:
        row = stored.get(cfg.id)
        entries.append(
            {
                "id": cfg.id,
                "name": cfg.name,
                "adapter": cfg.adapter,
                "url": cfg.url,
                "homepage": cfg.homepage,
                "status": source_status(cfg, env),
                "enabled": cfg.enabled,
                "islands": cfg.islands,
                "license": cfg.license,
                "priority": cfg.priority,
                "auth": {
                    "required": cfg.auth_env is not None,
                    "env": cfg.auth_env,
                    "configured": (
                        bool((env.get(cfg.auth_env) or "").strip()) if cfg.auth_env else None
                    ),
                },
                "last_result": {
                    "last_success_at": row["last_success_at"] if row else None,
                    "last_error": row["last_error"] if row else None,
                    "last_run_id": row["last_run_id"] if row else None,
                },
                "health": summarize(db.recent_source_runs(cfg.id, catalog.crawl.history_runs)),
                "notes": cfg.notes,
            }
        )
    return {
        "generated_at": now.isoformat(),
        "sources": entries,
        "candidates": [c.model_dump(mode="json") for c in catalog.candidates],
        "excluded": [c.model_dump(mode="json") for c in catalog.excluded],
    }


# --------------------------------------------------------------------------- orchestration


def export_all(
    db: Database,
    catalog: SourcesCatalog,
    window: DateWindow,
    report: dict[str, Any],
    output_dir: Path,
    *,
    now: datetime,
    env: Mapping[str, str] | None = None,
    island: str | None = None,
    venues_path: Path = DEFAULT_VENUES,
) -> dict[str, Path]:
    environment: Mapping[str, str] = os.environ if env is None else env
    events = events_in_window(db, window, island)
    try:
        venues = load_venues(venues_path)  # once per export; a missing file is an empty table
    except (OSError, yaml.YAMLError, ValueError) as exc:
        # An optional reference table must not block the export of what was just imported.
        logger.warning("venues table %s ignored: %s", venues_path, exc)
        venues = []
    paths = {
        "events": output_dir / "events.json",
        "csv": output_dir / "events.csv",
        "geojson": output_dir / "events.geojson",
        "import_report": output_dir / "import-report.json",
        "sources_report": output_dir / "sources-report.json",
    }
    write_atomic(paths["events"], _json_bytes(events_payload(db, events, venues)))
    write_atomic(paths["csv"], events_csv(db, events, venues))
    write_atomic(paths["geojson"], _json_bytes(events_geojson(events, venues)))
    write_atomic(paths["import_report"], _json_bytes(report))
    write_atomic(
        paths["sources_report"], _json_bytes(build_sources_report(catalog, db, now, environment))
    )
    return paths
