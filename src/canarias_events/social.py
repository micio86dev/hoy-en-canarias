"""Social source registry: which platforms can feed the importer, and which accounts run.

``config/social_sources.yaml`` documents every platform with its acquisition mode and holds the
verified accounts a person has chosen. Nothing is registered automatically. Modes that need
credentials, private sessions or a human (``requires_authorization``, ``manual_only``,
``excluded``) are never scheduled; ``import_url`` is a one-off human action.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from canarias_events.crawl_settings import parse_frequency
from canarias_events.geo import normalize_island, normalize_municipality
from canarias_events.sources.base import SourceConfig

AcquisitionMode = Literal[
    "api_public", "rss", "import_url", "manual_only", "requires_authorization", "excluded"
]
VerificationState = Literal["candidate", "verified", "rejected"]
Adapter = Literal["mastodon", "bluesky", "rss"]

# No credentials, no private session, no bypass: these are never fetched by the importer.
NOT_ACQUIRABLE = frozenset({"manual_only", "requires_authorization", "excluded"})
SCHEDULABLE_MODES = frozenset({"api_public", "rss"})
# Social events must never outrank an official source when dedup merges fields.
SOCIAL_PRIORITY = 10
BLUESKY_APPVIEW = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"

_RE_HOST = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$")
_RE_MASTODON = re.compile(
    r"^@?(?P<user>[A-Za-z0-9_.-]+)@(?P<host>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)$"
)
_RE_BLUESKY = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


class SocialSource(BaseModel):
    """One row of ``social_sources.yaml``: a platform description or a concrete account."""

    model_config = ConfigDict(extra="forbid")

    id: str
    platform: str
    name: str
    kind: Literal["platform", "account"] = "account"
    url: str | None = None
    handle: str | None = None  # mastodon: user@instance; bluesky: name.bsky.social
    adapter: Adapter | None = None
    link_hosts: list[str] = Field(default_factory=list)  # hosts whose event pages may be read
    year_policy: Literal["strict", "infer"] = "strict"
    islands: list[str] = Field(default_factory=list)
    municipalities: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    acquisition_mode: AcquisitionMode
    verification_state: VerificationState = "candidate"
    enabled: bool = False
    frequency: str | None = None
    permissions: str | None = None  # terms of service / licence reference
    notes: str | None = None
    last_checked: date | None = None

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"url must be http(s): {value}")
        return value.strip()

    @field_validator("frequency")
    @classmethod
    def _known_frequency(cls, value: str | None) -> str | None:
        if value is not None:
            parse_frequency(value)
        return value

    @field_validator("link_hosts")
    @classmethod
    def _bare_hosts(cls, value: list[str]) -> list[str]:
        hosts = [h.strip().lower() for h in value]
        for host in hosts:
            if not _RE_HOST.match(host):
                raise ValueError(f"link_hosts takes bare host names, got {host!r}")
        return hosts

    @field_validator("islands")
    @classmethod
    def _known_islands(cls, value: list[str]) -> list[str]:
        islands: list[str] = []
        for raw in value:
            island = normalize_island(raw)
            if island is None:
                raise ValueError(f"unknown island: {raw}")
            if island not in islands:
                islands.append(island)
        return islands

    @field_validator("municipalities")
    @classmethod
    def _known_municipalities(cls, value: list[str]) -> list[str]:
        names: list[str] = []
        for raw in value:
            name = normalize_municipality(raw)
            if name is None:
                raise ValueError(f"unknown municipality: {raw}")
            names.append(name)
        return names

    @model_validator(mode="after")
    def _rules(self) -> SocialSource:
        if self.acquisition_mode in NOT_ACQUIRABLE and (self.enabled or self.frequency):
            raise ValueError(
                f"{self.id}: mode {self.acquisition_mode} is never scheduled "
                "(no enabled flag, no frequency)"
            )
        if self.handle is not None:
            pattern = {"mastodon": _RE_MASTODON, "bluesky": _RE_BLUESKY}.get(self.adapter or "")
            if pattern is not None and pattern.match(self.handle) is None:
                raise ValueError(f"{self.id}: invalid {self.adapter} handle {self.handle!r}")
        return self


class SocialRegistry(BaseModel):
    sources: list[SocialSource] = Field(default_factory=list)


def load_social_registry(path: Path | str) -> SocialRegistry:
    """The registry in ``path``; a missing file is an empty registry."""
    file = Path(path)
    if not file.exists():
        return SocialRegistry()
    raw: Any = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    registry = SocialRegistry.model_validate(raw)
    seen: set[str] = set()
    for source in registry.sources:
        if source.id in seen:
            raise ValueError(f"duplicate social source id '{source.id}'")
        seen.add(source.id)
    return registry


def schedule_blocker(source: SocialSource) -> str | None:
    """Why ``source`` is never run by ``import-events`` (None: it is scheduled)."""
    if source.kind != "account":
        return "platform description"
    if source.acquisition_mode in NOT_ACQUIRABLE:
        return f"{source.acquisition_mode}: not acquirable"
    if source.acquisition_mode not in SCHEDULABLE_MODES:
        return f"{source.acquisition_mode}: human-driven"
    if source.verification_state != "verified":
        return f"{source.verification_state}: not verified"
    if not source.enabled:
        return "disabled"
    if source.adapter is None:
        return "no adapter"
    if source.adapter != "rss" and not source.handle:
        return "no handle"
    if source.adapter == "rss" and not source.url:
        return "no url"
    return None


def _endpoint(source: SocialSource) -> str:
    match = _RE_MASTODON.match(source.handle or "")
    if source.adapter == "mastodon" and match:
        return f"https://{match['host']}/@{match['user']}.rss"
    if source.adapter == "bluesky":
        return BLUESKY_APPVIEW
    return source.url or ""


def to_source_config(source: SocialSource) -> SourceConfig:
    """The pipeline's view of a scheduled account (low priority, social licence note)."""
    params: dict[str, Any] = {}
    if source.adapter in ("mastodon", "bluesky"):
        params = {
            "handle": source.handle, "link_hosts": source.link_hosts,
            "year_policy": source.year_policy,
        }  # fmt: skip
    return SourceConfig(
        id=source.id,
        name=source.name,
        adapter=source.adapter or "rss",
        url=_endpoint(source),
        enabled=True,
        islands=source.islands,
        municipality=source.municipalities[0] if len(source.municipalities) == 1 else None,
        categories=source.categories,
        license=source.permissions,
        homepage=source.url,
        notes=source.notes,
        priority=SOCIAL_PRIORITY,
        frequency=source.frequency,
        params=params,
    )


def scheduled_sources(registry: SocialRegistry) -> list[SourceConfig]:
    """Accounts the importer may run: verified, enabled, public RSS or API only."""
    return [to_source_config(s) for s in registry.sources if schedule_blocker(s) is None]
