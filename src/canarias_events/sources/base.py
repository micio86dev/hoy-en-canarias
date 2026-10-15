"""Adapter contract: configuration, fetch context, result container and shared helpers."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from canarias_events.date_range import DateWindow
from canarias_events.geo import island_for_coordinates, island_for_municipality, normalize_island
from canarias_events.http import SafeHttpClient
from canarias_events.models import Event

_PLACEHOLDERS = frozenset(
    {"", "-", "--", "---", "n/a", "na", "null", "none", "por determinar", "sin determinar"}
)


class SourceConfig(BaseModel):
    """One entry of ``config/sources.yaml``."""

    id: str
    name: str
    adapter: str
    url: str
    enabled: bool = True
    islands: list[str] = Field(default_factory=list)
    municipality: str | None = None
    categories: list[str] = Field(default_factory=list)
    license: str | None = None
    auth_env: str | None = None
    homepage: str | None = None
    notes: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"url must be http(s): {value}")
        return value.strip()

    @field_validator("islands")
    @classmethod
    def _known_islands(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for raw in value:
            island = normalize_island(raw)
            if island is None:
                raise ValueError(f"unknown island: {raw}")
            if island not in result:
                result.append(island)
        return result


@dataclass
class FetchContext:
    client: SafeHttpClient
    window: DateWindow
    now: datetime
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("canarias_events"))
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)


@dataclass
class SourceFetchResult:
    events: list[Event] = field(default_factory=list)
    records_received: int = 0
    discarded: list[tuple[str, str | None]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pages: int = 0

    def discard(self, reason: str, identifier: str | None) -> None:
        self.discarded.append((reason, identifier))


class SourceAdapter(ABC):
    """Fetches one source and maps its records to :class:`Event`.

    Errors (HTTP failures, invalid payloads) propagate as exceptions; the runner isolates them.
    """

    source_type: ClassVar[str]

    def __init__(self, config: SourceConfig) -> None:
        self.config = config

    @abstractmethod
    def fetch(self, ctx: FetchContext) -> SourceFetchResult: ...


# --------------------------------------------------------------------------- helpers


def clean_text(value: object) -> str | None:
    """Strip text and map empty values and placeholders such as '--' to None."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return None if text.casefold() in _PLACEHOLDERS else text


def resolve_island(
    config: SourceConfig,
    *,
    island: str | None = None,
    municipality: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> str | None:
    """Island by priority: item field, municipality (item then config), coordinates, config.

    The configured islands only count when there is exactly one. Never guesses.
    """
    resolved = normalize_island(island)
    if resolved is None:
        resolved = island_for_municipality(municipality)
    if resolved is None:
        resolved = island_for_municipality(config.municipality)
    if resolved is None:
        resolved = island_for_coordinates(latitude, longitude)
    if resolved is None and len(config.islands) == 1:
        resolved = config.islands[0]
    return resolved
