"""Global crawl behaviour: the optional ``crawl:`` section of ``sources.yaml``."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CrawlSettings(BaseModel):
    """Tunables with safe defaults; the file normally omits this section."""

    model_config = ConfigDict(extra="forbid")

    history_runs: int = Field(default=10, ge=1, le=200)  # runs compared by health and anomalies
