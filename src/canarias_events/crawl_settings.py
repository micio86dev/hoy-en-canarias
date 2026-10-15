"""Global crawl behaviour: the optional ``crawl:`` section of ``sources.yaml``."""

from __future__ import annotations

import re
from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field

_NAMED = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(weeks=1)}
_RE_AMOUNT = re.compile(r"^([1-9]\d{0,3})([hd])$")


def parse_frequency(value: str) -> timedelta:
    """``daily``, ``weekly``, ``hourly`` or ``<n>h`` / ``<n>d``; anything else is an error."""
    text = value.strip().lower()
    if text in _NAMED:
        return _NAMED[text]
    found = _RE_AMOUNT.match(text)
    if found is None:
        raise ValueError(f"invalid frequency {value!r}: use daily, weekly, hourly, 6h or 2d")
    amount = int(found.group(1))
    return timedelta(hours=amount) if found.group(2) == "h" else timedelta(days=amount)


class SnapshotSettings(BaseModel):
    """Raw listing of failed/anomalous runs for debugging; off by default."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_bytes: int = Field(default=500_000, ge=100, le=20_000_000)
    retention_days: int = Field(default=14, ge=0, le=3650)
    max_files: int = Field(default=10, ge=1, le=1000)  # per source


class CrawlSettings(BaseModel):
    """Tunables with safe defaults; the file normally omits this section."""

    model_config = ConfigDict(extra="forbid")

    history_runs: int = Field(default=10, ge=1, le=200)  # runs compared by health and anomalies
    # A run is anomalous when its event count falls by more than this share of the median of
    # the last successful runs (needs anomaly_min_runs of them, median >= anomaly_min_events).
    anomaly_drop_ratio: float = Field(default=0.7, gt=0, lt=1)
    anomaly_min_runs: int = Field(default=3, ge=2, le=50)
    anomaly_min_events: int = Field(default=5, ge=1)

    # After this many consecutive temporary failures a source is skipped with exponential backoff.
    failure_threshold: int = Field(default=3, ge=1, le=20)
    backoff_base_hours: float = Field(default=6.0, gt=0)
    backoff_max_hours: float = Field(default=168.0, gt=0)
    # Blocked, robots-disallowed and manual-review sources are not retried for this long.
    blocked_cooldown_hours: float = Field(default=72.0, ge=0)
    concurrency: int = Field(default=4, ge=1, le=16)  # sources fetched at the same time

    snapshots: SnapshotSettings = Field(default_factory=SnapshotSettings)
