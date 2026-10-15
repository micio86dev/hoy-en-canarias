"""Write the JSON/CSV event exports and the import and sources reports, atomically."""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from canarias_events.database import Database
from canarias_events.date_range import DateWindow
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
_CSV_COLUMNS[2:2] = []
_CSV_EXTRA = ["tags", "sources", "source_urls", "source_event_ids", "source_license"]


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


def events_payload(db: Database, events: list[Event]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for event in events:
        item = event.model_dump(mode="json", exclude={"raw_data"})
        item["sources"] = _source_links(db, event, raw=True)
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


def events_csv(db: Database, events: list[Event]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow([*_CSV_COLUMNS, *_CSV_EXTRA])
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
        writer.writerow(row)
    return CSV_BOM + buffer.getvalue().encode("utf-8")


# --------------------------------------------------------------------------- sources report


def _source_status(cfg: SourceConfig, env: Mapping[str, str]) -> str:
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
                "status": _source_status(cfg, env),
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
) -> dict[str, Path]:
    environment: Mapping[str, str] = os.environ if env is None else env
    events = events_in_window(db, window, island)
    paths = {
        "events": output_dir / "events.json",
        "csv": output_dir / "events.csv",
        "import_report": output_dir / "import-report.json",
        "sources_report": output_dir / "sources-report.json",
    }
    write_atomic(paths["events"], _json_bytes(events_payload(db, events)))
    write_atomic(paths["csv"], events_csv(db, events))
    write_atomic(paths["import_report"], _json_bytes(report))
    write_atomic(
        paths["sources_report"], _json_bytes(build_sources_report(catalog, db, now, environment))
    )
    return paths
