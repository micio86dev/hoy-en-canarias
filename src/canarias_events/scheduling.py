"""When a source is due: per-source frequency, failure backoff and the blocked cool-down."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from canarias_events.crawl_settings import CrawlSettings, parse_frequency
from canarias_events.database import SourceRun
from canarias_events.health import MANUAL_ACCEPT
from canarias_events.sources.base import SourceConfig
from canarias_events.statuses import BLOCKED, FAILED, NON_TEMPORARY, SUCCEEDED


def _is_non_temporary(run: SourceRun) -> bool:
    return run.status in NON_TEMPORARY or run.source_status == BLOCKED


def _hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600, 1)


def skip_reason(
    cfg: SourceConfig,
    runs: Sequence[SourceRun],
    last_success_at: str | None,
    now: datetime,
    settings: CrawlSettings,
) -> str | None:
    """Why ``cfg`` should not run now (None: it is due). ``runs`` are newest first."""
    # A manual accept is bookkeeping about the anomaly baseline, not an attempt to fetch.
    accepted = {r.started_at for r in runs if MANUAL_ACCEPT in r.anomalies}
    runs = [r for r in runs if MANUAL_ACCEPT not in r.anomalies]
    if last_success_at in accepted:  # the stored time came from such a row: use a real fetch
        last_success_at = next((r.started_at for r in runs if r.status == SUCCEEDED), None)
    if cfg.frequency and last_success_at:
        age = now - datetime.fromisoformat(last_success_at)
        if age < parse_frequency(cfg.frequency):
            return f"frequency {cfg.frequency}: last success {_hours(age)}h ago"
    if not runs:
        return None
    latest = runs[0]
    if _is_non_temporary(latest):
        age = now - datetime.fromisoformat(latest.started_at)
        if age < timedelta(hours=settings.blocked_cooldown_hours):
            return (
                f"cool-down: {latest.source_status or latest.status} {_hours(age)}h ago "
                f"(retry after {settings.blocked_cooldown_hours:g}h or use --force)"
            )
        return None
    streak = 0
    for run in runs:
        if run.status != FAILED or _is_non_temporary(run):
            break
        streak += 1
    if streak < settings.failure_threshold:
        return None
    wait = timedelta(
        hours=min(
            settings.backoff_base_hours * 2 ** (streak - settings.failure_threshold),
            settings.backoff_max_hours,
        )
    )
    elapsed = now - datetime.fromisoformat(latest.started_at)
    if elapsed < wait:
        return (
            f"backoff: {streak} consecutive failures, next attempt in "
            f"{_hours(wait - elapsed)}h (use --force to retry now)"
        )
    return None
