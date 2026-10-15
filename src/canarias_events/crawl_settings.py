"""Global crawl behaviour: the optional ``crawl:`` section of ``sources.yaml``."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CrawlSettings(BaseModel):
    """Tunables with safe defaults; the file normally omits this section."""

    model_config = ConfigDict(extra="forbid")

    history_runs: int = Field(default=10, ge=1, le=200)  # runs compared by health and anomalies
    # A run is anomalous when its event count falls by more than this share of the median of
    # the last successful runs (needs anomaly_min_runs of them, median >= anomaly_min_events).
    anomaly_drop_ratio: float = Field(default=0.7, gt=0, lt=1)
    anomaly_min_runs: int = Field(default=3, ge=2, le=50)
    anomaly_min_events: int = Field(default=5, ge=1)
