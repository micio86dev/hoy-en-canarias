"""Import pipeline: run sources in isolation, filter, deduplicate, upsert and build the report."""

from __future__ import annotations

import logging
import os
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from canarias_events.database import Database, UpsertResult
from canarias_events.date_range import DateWindow
from canarias_events.deduplication import DEFAULT_MERGE_THRESHOLD, resolve
from canarias_events.geo import normalize_island
from canarias_events.http import SafeHttpClient
from canarias_events.models import Event
from canarias_events.sources.base import FetchContext, SourceAdapter, SourceConfig
from canarias_events.sources.registry import create_adapter

logger = logging.getLogger("canarias_events.pipeline")

OUTSIDE_WINDOW = "outside window"
MISSING_START = "missing start date"
ISLAND_FILTER = "island filter"

AdapterFactory = Callable[[SourceConfig], SourceAdapter]


def event_overlaps(window: DateWindow, event: Event) -> bool | None:
    """True/False for the window test; None when the event has no start at all."""
    start = event.start_datetime or event.start_date
    if start is None:
        return None
    end = event.end_datetime or event.end_date
    return window.overlaps(start, end, event.all_day or event.start_datetime is None)


def events_in_window(db: Database, window: DateWindow, island: str | None = None) -> list[Event]:
    """Stored events overlapping the window (optionally one island), in display order."""
    found = [
        e
        for e in db.all_events()
        if event_overlaps(window, e) and (island is None or e.island == island)
    ]
    found.sort(key=lambda e: (e.start_date or window.start_date, str(e.start_datetime), e.title))
    return found


def _new_stats() -> dict[str, Any]:
    return {
        "status": "succeeded",
        "records_received": 0,
        "valid_events": 0,
        "in_window": 0,
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "merged": 0,
        "possible_duplicates": 0,
        "discarded": {},
        "duration_s": 0.0,
        "pages": 0,
        "warnings": [],
        "error": None,
    }


def _select(
    sources: Sequence[SourceConfig],
    source_ids: Sequence[str] | None,
    island: str | None,
    env: Mapping[str, str],
) -> tuple[list[SourceConfig], list[dict[str, str]]]:
    known = {s.id for s in sources}
    unknown = [i for i in (source_ids or []) if i not in known]
    if unknown:
        raise ValueError(f"unknown source(s): {', '.join(unknown)}")
    chosen: list[SourceConfig] = []
    skipped: list[dict[str, str]] = []
    for cfg in sources:
        if source_ids and cfg.id not in source_ids:
            continue
        reason: str | None = None
        if not cfg.enabled:
            reason = "disabled"
        elif island and cfg.islands and island not in cfg.islands:
            reason = "serves other islands"
        elif cfg.auth_env and not (env.get(cfg.auth_env) or "").strip():
            reason = f"missing API key ({cfg.auth_env})"
        if reason:
            skipped.append({"id": cfg.id, "reason": reason})
        else:
            chosen.append(cfg)
    return chosen, skipped


def _ingest(
    db: Database,
    events: list[Event],
    cfg: SourceConfig,
    window: DateWindow,
    island: str | None,
    now: datetime,
    threshold: float,
    stats: dict[str, Any],
) -> None:
    discarded: Counter[str] = Counter(stats["discarded"])
    for event in events:
        inside = event_overlaps(window, event)
        if inside is None:
            discarded[MISSING_START] += 1
            continue
        if not inside:
            discarded[OUTSIDE_WINDOW] += 1
            continue
        if island and event.island != island:
            discarded[ISLAND_FILTER] += 1
            continue
        stats["in_window"] += 1
        decision = resolve(db, event, cfg.id, merge_threshold=threshold)
        result: UpsertResult = db.upsert(event, cfg.id, decision, now)
        stats[result.outcome] += 1
        stats["possible_duplicates"] += result.possible_duplicates
    stats["discarded"] = dict(discarded)


def run_import(
    db: Database,
    sources: Sequence[SourceConfig],
    window: DateWindow,
    *,
    client: SafeHttpClient,
    now: datetime,
    adapter_factory: AdapterFactory = create_adapter,
    island: str | None = None,
    source_ids: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
) -> dict[str, Any]:
    """Run every selected source, isolate failures and return (and store) the import report."""
    environment: Mapping[str, str] = os.environ if env is None else env
    island_name = None
    if island:
        island_name = normalize_island(island)
        if island_name is None:
            raise ValueError(f"unknown island: {island}")
    chosen, skipped = _select(sources, source_ids, island_name, environment)

    db.sync_sources(list(sources))
    params = {
        "island": island_name,
        "sources": list(source_ids) if source_ids else None,
        "merge_threshold": merge_threshold,
    }
    run_id = db.start_run(now, window.start, window.end, params)
    per_source: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    for cfg in chosen:
        stats = _new_stats()
        per_source[cfg.id] = stats
        began = time.monotonic()
        try:
            ctx = FetchContext(client=client, window=window, now=now, env=environment)
            result = adapter_factory(cfg).fetch(ctx)
            stats["records_received"] = result.records_received
            stats["valid_events"] = len(result.events)
            stats["pages"] = result.pages
            stats["warnings"] = list(result.warnings)
            stats["discarded"] = dict(Counter(reason for reason, _ in result.discarded))
            _ingest(db, result.events, cfg, window, island_name, now, merge_threshold, stats)
        except Exception as exc:  # isolation: one broken source must not stop the run
            stats["status"] = "failed"
            stats["error"] = f"{type(exc).__name__}: {exc}"
            errors.append({"source": cfg.id, "error": stats["error"]})
            logger.error("source %s failed: %s", cfg.id, stats["error"])
        stats["duration_s"] = round(time.monotonic() - began, 3)
        warnings.extend({"source": cfg.id, "warning": w} for w in stats["warnings"])
        db.record_source_result(cfg.id, run_id, error=stats["error"], now=now)
        logger.info("source %s: %s", cfg.id, stats["status"])

    def total(key: str) -> int:
        return sum(int(s[key]) for s in per_source.values())

    discarded_total: Counter[str] = Counter()
    for stats in per_source.values():
        discarded_total.update(stats["discarded"])
    report: dict[str, Any] = {
        "run_id": run_id,
        "executed_at": now.isoformat(),
        "window": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "start_date": window.start_date.isoformat(),
            "end_date_inclusive": window.end_date_inclusive.isoformat(),
        },
        "params": params,
        "sources_attempted": [c.id for c in chosen],
        "sources_succeeded": [i for i, s in per_source.items() if s["status"] == "succeeded"],
        "sources_failed": [i for i, s in per_source.items() if s["status"] == "failed"],
        "sources_skipped": skipped,
        "sources": per_source,
        "totals": {
            "events_in_window": len(events_in_window(db, window, island_name)),
            "records_received": total("records_received"),
            "valid_events": total("valid_events"),
            "in_window": total("in_window"),
            "new": total("new"),
            "updated": total("updated"),
            "unchanged": total("unchanged"),
            "merged": total("merged"),
            "possible_duplicates": total("possible_duplicates"),
            "discarded": dict(discarded_total),
        },
        "errors": errors,
        "warnings": warnings,
    }
    db.finish_run(run_id, now, report)
    return report
