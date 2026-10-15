"""Import pipeline: run sources in isolation, filter, deduplicate, upsert and build the report."""

from __future__ import annotations

import logging
import os
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from canarias_events.crawl_settings import CrawlSettings
from canarias_events.database import Database, SourceRun, UpsertResult
from canarias_events.date_range import TZ, DateWindow
from canarias_events.deduplication import DEFAULT_MERGE_THRESHOLD, resolve
from canarias_events.extract.llm import AiSettings
from canarias_events.geo import normalize_island
from canarias_events.health import INFORMATIONAL, detect_anomalies
from canarias_events.http import HttpError, SafeHttpClient
from canarias_events.models import Event
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceConfig,
    SourceFetchResult,
)
from canarias_events.sources.registry import create_adapter

logger = logging.getLogger("canarias_events.pipeline")

OUTSIDE_WINDOW = "outside window"
MISSING_START = "missing start date"
ISLAND_FILTER = "island filter"

# Adapter statuses that are neither success nor a crash: a human has to look at the source.
NEEDS_REVIEW = frozenset({"robots_disallowed", "needs_manual_review"})
PERMANENT_HTTP = frozenset({401, 403})  # access denied: never retried as a temporary error

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
    far = datetime.max.replace(tzinfo=TZ)
    found.sort(key=lambda e: (e.start_date or window.start_date, e.start_datetime or far, e.title))
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
        "anomalies": [],
        "quarantined": 0,
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


def _record_extraction(stats: dict[str, Any], result: SourceFetchResult) -> None:
    """Acquisition details of adapters that try several strategies (the ``web`` adapter)."""
    stats["source_status"] = result.status
    if result.strategy_used is None and result.extraction_ok is None and not result.tiers:
        return
    stats["strategy"] = result.strategy_used
    stats["extraction_ok"] = result.extraction_ok
    stats["listing_hash"] = result.listing_hash
    stats["tiers"] = list(result.tiers)


def _in_window(
    events: list[Event],
    window: DateWindow,
    island: str | None,
    stats: dict[str, Any],
) -> list[Event]:
    """Events that belong in this run's window; counts what is dropped and why."""
    discarded: Counter[str] = Counter(stats["discarded"])
    kept: list[Event] = []
    for event in events:
        inside = event_overlaps(window, event)
        if inside is None:
            discarded[MISSING_START] += 1
        elif not inside:
            discarded[OUTSIDE_WINDOW] += 1
        elif island and event.island != island:
            discarded[ISLAND_FILTER] += 1
        else:
            kept.append(event)
    stats["in_window"] += len(kept)
    stats["discarded"] = dict(discarded)
    return kept


def _ingest(
    db: Database,
    events: list[Event],
    source_id: str,
    now: datetime,
    threshold: float,
    stats: dict[str, Any],
) -> None:
    for event in events:
        decision = resolve(db, event, source_id, merge_threshold=threshold)
        result: UpsertResult = db.upsert(event, source_id, decision, now)
        stats[result.outcome] += 1
        stats["possible_duplicates"] += result.possible_duplicates


def accept_quarantine(
    db: Database, source_id: str, *, now: datetime, threshold: float = DEFAULT_MERGE_THRESHOLD
) -> int:
    """Publish the events held back for ``source_id`` (a manual decision); returns how many."""
    pending = db.quarantine_pending(source_id)
    stats: dict[str, Any] = {k: 0 for k in ("new", "updated", "unchanged", "merged")}
    stats["possible_duplicates"] = 0
    _ingest(db, [item.event for item in pending], source_id, now, threshold, stats)
    db.quarantine_close(source_id, "accepted")
    return len(pending)


def _apply_result(
    db: Database,
    cfg: SourceConfig,
    result: SourceFetchResult,
    history: Sequence[SourceRun],
    run: _Run,
    stats: dict[str, Any],
) -> None:
    """Map one adapter result to a status, then ingest it or hold it back."""
    stats["records_received"] = result.records_received
    stats["valid_events"] = len(result.events)
    stats["pages"] = result.pages
    stats["warnings"] = list(result.warnings)
    stats["discarded"] = dict(Counter(reason for reason, _ in result.discarded))
    _record_extraction(stats, result)
    events = _in_window(result.events, run.window, run.island, stats)
    codes = detect_anomalies(
        history,
        events_found=len(result.events),
        extraction_ok=result.extraction_ok,
        strategy=result.strategy_used,
        listing_hash=result.listing_hash,
        source_status=result.status,
        settings=run.settings,
    )
    stats["anomalies"] = codes
    reason = ",".join(c for c in codes if c not in INFORMATIONAL)
    hold: str | None = None  # why the events are held back instead of published
    if result.status == "blocked":  # 401/403/429 or challenge page: not bypassed
        message = next((w for w in result.warnings if w.startswith("blocked")), "blocked: denied")
        stats["error"] = message
        if result.events:  # partial data from a source that then blocked us: doubtful
            stats["status"], hold = "partial", "blocked_partial"
        else:
            stats["status"] = "failed"
            run.errors.append({"source": cfg.id, "error": message})
    elif result.status in NEEDS_REVIEW:  # not a healthy empty source, not a crash
        stats["status"] = result.status
        stats["error"] = result.warnings[-1] if result.warnings else result.status
    elif reason:
        stats["status"], hold = "anomalous", reason
        stats["error"] = f"anomaly: {reason}"
        run.warnings.append({"source": cfg.id, "warning": stats["error"]})
    if hold is not None:
        stats["quarantined"] = db.quarantine_add(cfg.id, run.run_id, hold, events, run.now)
        return
    if stats["status"] == "succeeded":
        _ingest(db, events, cfg.id, run.now, run.threshold, stats)
        db.quarantine_close(cfg.id, "superseded")  # a normal run confirms the source again


@dataclass
class _Run:
    """What one import run shares between its sources."""

    run_id: int
    now: datetime
    window: DateWindow
    island: str | None
    threshold: float
    settings: CrawlSettings
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)


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
    ai: AiSettings | None = None,
    crawl: CrawlSettings | None = None,
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

    run = _Run(run_id, now, window, island_name, merge_threshold, crawl or CrawlSettings())
    for cfg in chosen:
        stats = _new_stats()
        per_source[cfg.id] = stats
        began = time.monotonic()
        history = db.recent_source_runs(cfg.id, run.settings.history_runs)
        try:
            ctx = FetchContext(client=client, window=window, now=now, env=environment, ai=ai)
            _apply_result(db, cfg, adapter_factory(cfg).fetch(ctx), history, run, stats)
        except Exception as exc:  # isolation: one broken source must not stop the run
            stats["status"] = "failed"
            if isinstance(exc, HttpError) and exc.status in PERMANENT_HTTP:
                stats["source_status"] = "blocked"
            stats["error"] = f"{type(exc).__name__}: {exc}"
            run.errors.append({"source": cfg.id, "error": stats["error"]})
            logger.error("source %s failed: %s", cfg.id, stats["error"])
        stats["duration_s"] = round(time.monotonic() - began, 3)
        run.warnings.extend({"source": cfg.id, "warning": w} for w in stats["warnings"])
        db.record_source_result(cfg.id, run_id, error=stats["error"], now=now)
        db.record_source_run(
            run_id, cfg.id, now, status=stats["status"], source_status=stats.get("source_status"),
            events_found=stats["valid_events"], events_in_window=stats["in_window"],
            duration_s=stats["duration_s"], error=stats["error"], strategy=stats.get("strategy"),
            extraction_ok=stats.get("extraction_ok"), listing_hash=stats.get("listing_hash"),
            anomalies=stats["anomalies"],
        )  # fmt: skip
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
        "sources_partial": [i for i, s in per_source.items() if s["status"] == "partial"],
        "sources_anomalous": [i for i, s in per_source.items() if s["status"] == "anomalous"],
        "sources_needs_review": [i for i, s in per_source.items() if s["status"] in NEEDS_REVIEW],
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
        "errors": run.errors,
        "warnings": run.warnings,
    }
    db.finish_run(run_id, now, report)
    return report
