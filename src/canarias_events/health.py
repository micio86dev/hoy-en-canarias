"""Per-source health computed from the stored run history (plain functions, no state)."""

from __future__ import annotations

from collections.abc import Sequence
from statistics import median
from typing import Any

from canarias_events.crawl_settings import CrawlSettings
from canarias_events.database import SourceRun
from canarias_events.statuses import NO_CONTENT_STATUSES, SUCCEEDED

EMPTY_MARKER = "empty-marker"  # strategy of a page that says, in its own words, "no events"
# Codes that only inform; every other code keeps the run's events out of the exports.
INFORMATIONAL = frozenset({"strategy_changed"})


def is_blocking(codes: Sequence[str]) -> bool:
    return any(code not in INFORMATIONAL for code in codes)


def detect_anomalies(
    history: Sequence[SourceRun],
    *,
    events_found: int,
    extraction_ok: bool | None,
    strategy: str | None,
    listing_hash: str | None,
    source_status: str,
    settings: CrawlSettings,
) -> list[str]:
    """Deterministic comparison of this run with the source's own history (newest first).

    An empty page only counts as "no events" when extraction worked (``extraction_ok``) or the
    page declared itself empty; nothing is compared for sources that were never read.
    """
    if source_status in NO_CONTENT_STATUSES:
        return []
    good = [r for r in history if r.status == SUCCEEDED][: settings.history_runs]
    if not good:
        return []
    last = good[0]
    codes: list[str] = []
    if events_found == 0:
        if last.events_found > 0 and strategy != EMPTY_MARKER:
            if extraction_ok is False:
                codes.append("extraction_failed")
            elif listing_hash and last.listing_hash and listing_hash != last.listing_hash:
                codes.append("layout_changed")
            else:
                codes.append("empty_after_events")
    elif len(good) >= settings.anomaly_min_runs:
        base = median(r.events_found for r in good)
        drop = round(1 - events_found / base, 6) if base else 0.0
        if base >= settings.anomaly_min_events and drop > settings.anomaly_drop_ratio:
            codes.append("count_drop")
    if events_found > 0 and strategy and last.strategy and strategy != last.strategy:
        codes.append("strategy_changed")
    return codes


def summarize(runs: Sequence[SourceRun]) -> dict[str, Any]:
    """Health of one source from its latest runs (newest first)."""
    if not runs:
        return {
            "runs_considered": 0,
            "last_status": None,
            "last_events_found": None,
            "last_success_at": None,
            "error_rate": None,
            "consecutive_failures": 0,
            "last_anomalies": [],
        }
    streak = 0
    for run in runs:
        if run.status == SUCCEEDED:
            break
        streak += 1
    last_ok = next((r for r in runs if r.status == SUCCEEDED), None)
    failed = sum(1 for r in runs if r.status != SUCCEEDED)
    return {
        "runs_considered": len(runs),
        "last_status": runs[0].status,
        "last_events_found": last_ok.events_found if last_ok else None,
        "last_success_at": last_ok.started_at if last_ok else None,
        "error_rate": round(failed / len(runs), 3),
        "consecutive_failures": streak,
        "last_anomalies": list(runs[0].anomalies),
    }
