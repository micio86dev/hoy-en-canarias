"""Adapter registry and ``sources.yaml`` loading."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from canarias_events.crawl_settings import CrawlSettings
from canarias_events.extract.llm import AiSettings
from canarias_events.sources.base import SourceAdapter, SourceConfig
from canarias_events.sources.ckan import CkanAdapter
from canarias_events.sources.drupal_jsonapi import DrupalJsonApiAdapter
from canarias_events.sources.ical import IcalAdapter
from canarias_events.sources.rss import RssAdapter
from canarias_events.sources.ticketmaster import TicketmasterAdapter
from canarias_events.sources.tribe_events import TribeEventsAdapter
from canarias_events.sources.web import WebAdapter

ADAPTERS: dict[str, type[SourceAdapter]] = {
    "ckan": CkanAdapter,
    "drupal_jsonapi": DrupalJsonApiAdapter,
    "ical": IcalAdapter,
    "rss": RssAdapter,
    "ticketmaster": TicketmasterAdapter,
    "tribe_events": TribeEventsAdapter,
    "web": WebAdapter,
}


class CatalogEntry(BaseModel):
    """A candidate or excluded source documented for the sources report."""

    name: str
    url: str | None = None
    reason: str
    checked_at: date | None = None


class SourcesCatalog(BaseModel):
    sources: list[SourceConfig] = Field(default_factory=list)
    candidates: list[CatalogEntry] = Field(default_factory=list)
    excluded: list[CatalogEntry] = Field(default_factory=list)
    discovery_probes: list[str] = Field(default_factory=list)
    ai: AiSettings = Field(default_factory=AiSettings)  # optional local model, off by default
    crawl: CrawlSettings = Field(default_factory=CrawlSettings)


def create_adapter(config: SourceConfig) -> SourceAdapter:
    try:
        adapter_cls = ADAPTERS[config.adapter]
    except KeyError:
        raise ValueError(f"unknown adapter '{config.adapter}' for source '{config.id}'") from None
    return adapter_cls(config)


def load_catalog(path: Path | str) -> SourcesCatalog:
    raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    catalog = SourcesCatalog.model_validate(raw)
    seen: set[str] = set()
    for source in catalog.sources:
        if source.adapter not in ADAPTERS:
            raise ValueError(f"unknown adapter '{source.adapter}' for source '{source.id}'")
        if source.id in seen:
            raise ValueError(f"duplicate source id '{source.id}'")
        seen.add(source.id)
    return catalog


def load_sources(path: Path | str) -> list[SourceConfig]:
    return load_catalog(path).sources
