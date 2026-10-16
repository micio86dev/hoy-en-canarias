"""Import pipeline: run sources in isolation, filter, deduplicate, upsert and build the report."""

from __future__ import annotations

import logging
import os
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from canarias_events.crawl_settings import LOW_RESOURCE_PAGE_CAP, CrawlSettings
from canarias_events.database import Database, SourceRun, UpsertResult
from canarias_events.date_range import TZ, DateWindow
from canarias_events.deduplication import DEFAULT_MERGE_THRESHOLD, resolve
from canarias_events.extract.llm import AiSettings
from canarias_events.geo import normalize_island
from canarias_events.health import BASELINE_RESET, INFORMATIONAL, MANUAL_ACCEPT, detect_anomalies
from canarias_events.http import HttpError, SafeHttpClient
from canarias_events.models import Event
from canarias_events.scheduling import skip_reason
from canarias_events.snapshots import prune_snapshots, save_snapshot
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceConfig,
    SourceFetchResult,
)
from canarias_events.sources.registry import create_adapter
from canarias_events.statuses import (
    ADAPTER_OK,
    ANOMALOUS,
    BLOCKED,
    BLOCKED_PARTIAL,
    FAILED,
    NEEDS_REVIEW,
    NO_LEVEL_HOLDS,
    PARTIAL,
    RATE_LIMITED,
    SUCCEEDED,
)

logger = logging.getLogger("canarias_events.pipeline")

OUTSIDE_WINDOW = "outside window"
MISSING_START = "missing start date"
IMPLAUSIBLE_DATE = "implausible date"

# Dates outside these bounds are source typos (e.g. year 0026), not real events.
EARLIEST_PLAUSIBLE_YEAR = 2000
MAX_PLAUSIBLE_SPAN_DAYS = 731  # the longest real runs seen (dinner shows, seasons) last ~14 months
ISLAND_FILTER = "island filter"

PERMANENT_HTTP = frozenset({401, 403})  # access denied: never retried as a temporary error

AdapterFactory = Callable[[SourceConfig], SourceAdapter]


def event_overlaps(window: DateWindow, event: Event) -> bool | None:
    """True/False for the window test; None when the event has no start at all."""
    start = event.start_datetime or event.start_date
    if start is None:
        return None
    end = event.end_datetime or event.end_date
    return window.overlaps(start, end, event.all_day or event.start_datetime is None)


def implausible_dates(event: Event) -> bool:
    """True when the dates look like a source typo: an ancient year or a multi-year span."""
    start = event.start_date or (event.start_datetime.date() if event.start_datetime else None)
    if start is None:
        return False
    end: date | None = event.end_date or (
        event.end_datetime.date() if event.end_datetime else None
    )
    if start.year < EARLIEST_PLAUSIBLE_YEAR:
        return True
    return end is not None and (end - start).days > MAX_PLAUSIBLE_SPAN_DAYS


def events_in_window(db: Database, window: DateWindow, island: str | None = None) -> list[Event]:
    """Stored events overlapping the window (optionally one island), in display order."""
    found = [
        e
        for e in db.all_events()
        if event_overlaps(window, e)
        and not implausible_dates(e)
        and (island is None or e.island == island)
    ]
    far = datetime.max.replace(tzinfo=TZ)
    found.sort(key=lambda e: (e.start_date or window.start_date, e.start_datetime or far, e.title))
    return found


def _new_stats() -> dict[str, Any]:
    return {
        "status": SUCCEEDED,
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
        elif implausible_dates(event):
            discarded[IMPLAUSIBLE_DATE] += 1
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


def ingest_events(
    db: Database,
    events: list[Event],
    source_id: str,
    now: datetime,
    threshold: float = DEFAULT_MERGE_THRESHOLD,
) -> dict[str, Any]:
    """Deduplicate and store ``events`` of one source; returns the outcome counters."""
    stats = _new_stats()
    _ingest(db, events, source_id, now, threshold, stats)
    return stats


def accept_quarantine(
    db: Database, source_id: str, *, now: datetime, threshold: float = DEFAULT_MERGE_THRESHOLD
) -> int:
    """Publish the events held back for ``source_id`` (a manual decision); returns how many."""
    pending = db.quarantine_pending(source_id)
    stats = _new_stats()
    _ingest(db, [item.event for item in pending], source_id, now, threshold, stats)
    db.quarantine_close(source_id, "accepted")
    # The accepted level of the latest quarantining run is the new baseline; partial data from
    # a blocked run and one-off imports are no level, so they neither count nor reset anything.
    level = [item for item in pending if item.reason not in NO_LEVEL_HOLDS]
    if level:
        latest = max(item.run_id for item in level)
        count = sum(1 for item in level if item.run_id == latest)
        db.record_source_run(
            latest, source_id, now, status=SUCCEEDED, source_status=ADAPTER_OK,
            events_found=count, events_in_window=count, duration_s=0.0,
            error=None, strategy=None, extraction_ok=None, listing_hash=None,
            anomalies=[BASELINE_RESET, MANUAL_ACCEPT],
        )  # fmt: skip
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
    if result.status in (BLOCKED, RATE_LIMITED):  # 401/403/429 or challenge page: not bypassed
        message = next(
            (w for w in result.warnings if w.startswith(result.status)), f"{result.status}: denied"
        )
        stats["error"] = message
        if result.events:  # partial data from a source that then blocked us: doubtful
            stats["status"], hold = PARTIAL, BLOCKED_PARTIAL
        else:
            stats["status"] = FAILED
            run.errors.append({"source": cfg.id, "error": message})
    elif result.status in NEEDS_REVIEW:  # not a healthy empty source, not a crash
        stats["status"] = result.status
        stats["error"] = result.warnings[-1] if result.warnings else result.status
    elif reason:
        stats["status"], hold = ANOMALOUS, reason
        stats["error"] = f"anomaly: {reason}"
        run.warnings.append({"source": cfg.id, "warning": stats["error"]})
    _keep_snapshot(cfg, result, run, stats)
    if hold is not None:
        stats["quarantined"] = db.quarantine_add(cfg.id, run.run_id, hold, events, run.now)
        return
    if stats["status"] == SUCCEEDED:
        _ingest(db, events, cfg.id, run.now, run.threshold, stats)
        db.quarantine_close(cfg.id, "superseded")  # a normal run confirms the source again


def _keep_snapshot(
    cfg: SourceConfig, result: SourceFetchResult, run: _Run, stats: dict[str, Any]
) -> None:
    """Save the raw listing of a run that did not end healthy (optional, see ``snapshots``)."""
    if (
        not run.settings.snapshots.enabled
        or run.snapshot_dir is None
        or result.snapshot is None
        or stats["status"] == SUCCEEDED
    ):
        return
    path = save_snapshot(run.snapshot_dir, cfg.id, result.snapshot, run.now, run.settings.snapshots)
    if path is not None:
        stats["snapshot"] = str(path)


@dataclass
class _Fetched:
    """Outcome of fetching one source in a worker thread."""

    result: SourceFetchResult | None = None
    error: Exception | None = None
    seconds: float = 0.0


def _due(
    db: Database, chosen: list[SourceConfig], now: datetime, settings: CrawlSettings, *, force: bool
) -> tuple[list[SourceConfig], list[dict[str, str]]]:
    """Split the chosen sources into those to run now and those deferred (frequency, backoff)."""
    if force:
        return chosen, []
    stored = db.source_rows()
    due: list[SourceConfig] = []
    deferred: list[dict[str, str]] = []
    depth = max(settings.history_runs, settings.failure_threshold + 8)
    for cfg in chosen:
        row = stored.get(cfg.id)
        reason = skip_reason(
            cfg,
            db.recent_source_runs(cfg.id, depth),
            row["last_success_at"] if row else None,
            now,
            settings,
        )
        if reason:
            deferred.append({"id": cfg.id, "reason": reason})
        else:
            due.append(cfg)
    return due, deferred


@dataclass
class _Run:
    """What one import run shares between its sources."""

    run_id: int
    now: datetime
    window: DateWindow
    island: str | None
    threshold: float
    settings: CrawlSettings
    snapshot_dir: Path | None = None
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
    force: bool = False,
    snapshot_dir: Path | None = None,
    low_resource: bool = False,
) -> dict[str, Any]:
    """Run every selected source, isolate failures and return (and store) the import report."""
    environment: Mapping[str, str] = os.environ if env is None else env
    settings = crawl or CrawlSettings()
    low = low_resource or settings.low_resource
    if low:  # gentle on RAM and CPU: serial, static pages only, no local model
        settings = settings.model_copy(update={"concurrency": 1})
        ai = None
    started = time.monotonic()
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
        "concurrency": settings.concurrency,
        "low_resource": low,
        "force": force,
    }
    run_id = db.start_run(now, window.start, window.end, params)
    per_source: dict[str, dict[str, Any]] = {}

    run = _Run(run_id, now, window, island_name, merge_threshold, settings, snapshot_dir)
    if snapshot_dir is not None and settings.snapshots.enabled:
        prune_snapshots(snapshot_dir, settings.snapshots, now)
    due, deferred = _due(db, chosen, now, settings, force=force)
    skipped.extend(deferred)

    def fetch(cfg: SourceConfig) -> _Fetched:  # runs in a worker thread: network only, no DB
        began = time.monotonic()
        try:
            ctx = FetchContext(
                client=client, window=window, now=now, env=environment, ai=ai,
                allow_render=not low, page_cap=LOW_RESOURCE_PAGE_CAP if low else None,
            )  # fmt: skip
            result = adapter_factory(cfg).fetch(ctx)
            return _Fetched(result=result, seconds=time.monotonic() - began)
        except Exception as exc:  # isolation: one broken source must not stop the run
            return _Fetched(error=exc, seconds=time.monotonic() - began)

    with ThreadPoolExecutor(max_workers=max(1, min(settings.concurrency, len(due) or 1))) as pool:
        fetched = list(pool.map(fetch, due))  # map keeps the configured order

    for cfg, got in zip(due, fetched, strict=True):
        stats = _new_stats()
        per_source[cfg.id] = stats
        began = time.monotonic()
        history = db.recent_source_runs(cfg.id, settings.history_runs)
        try:
            if got.error is not None:
                raise got.error
            if got.result is None:  # pragma: no cover - fetch() always sets one of the two
                raise RuntimeError("adapter returned no result")
            _apply_result(db, cfg, got.result, history, run, stats)
        except Exception as exc:
            stats["status"] = FAILED
            if isinstance(exc, HttpError) and exc.status in PERMANENT_HTTP:
                stats["source_status"] = BLOCKED
            stats["error"] = f"{type(exc).__name__}: {exc}"
            run.errors.append({"source": cfg.id, "error": stats["error"]})
            logger.error("source %s failed: %s", cfg.id, stats["error"])
        store = time.monotonic() - began
        stats["timings"] = {"fetch_s": round(got.seconds, 3), "store_s": round(store, 3)}
        stats["duration_s"] = round(got.seconds + store, 3)
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

    if snapshot_dir is not None and settings.snapshots.enabled:
        prune_snapshots(snapshot_dir, settings.snapshots, now)

    def total(key: str) -> int:
        return sum(int(s[key]) for s in per_source.values())

    discarded_total: Counter[str] = Counter()
    for stats in per_source.values():
        discarded_total.update(stats["discarded"])
    tier_seconds: dict[str, float] = {}
    for stats in per_source.values():
        for tier in stats.get("tiers", []):
            name = str(tier["tier"])
            tier_seconds[name] = tier_seconds.get(name, 0.0) + float(tier["seconds"])
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
        "sources_attempted": [c.id for c in due],
        "sources_succeeded": [i for i, s in per_source.items() if s["status"] == SUCCEEDED],
        "sources_failed": [i for i, s in per_source.items() if s["status"] == FAILED],
        "sources_partial": [i for i, s in per_source.items() if s["status"] == PARTIAL],
        "sources_anomalous": [i for i, s in per_source.items() if s["status"] == ANOMALOUS],
        "sources_needs_review": [i for i, s in per_source.items() if s["status"] in NEEDS_REVIEW],
        "sources_skipped": skipped,
        "sources_deferred": [d["id"] for d in deferred],  # not due: frequency, backoff, cool-down
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
        "timings": {
            "total_s": round(time.monotonic() - started, 3),
            "sources": {i: s["duration_s"] for i, s in per_source.items()},
            "tiers": {t: round(v, 3) for t, v in tier_seconds.items()},
        },
    }
    db.finish_run(run_id, now, report)
    return report
