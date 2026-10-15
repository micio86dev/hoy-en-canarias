"""Shared rules that turn public social posts into events (Mastodon, Bluesky).

A post is not an event. One becomes an event only when its text holds a deterministic date
(``dates_es``) AND either

* it links to an event page (on a host the person listed in ``link_hosts``) whose JSON-LD has
  an Event on that very day and month, or
* the same text names the place explicitly ("en Adeje").

Everything else is skipped and reported. Events made here carry ``extra.source_trust =
"social"``: deduplication never merges them by fuzzy match and, through the low source
priority, they never overwrite fields of reliable sources.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from canarias_events.date_range import TZ
from canarias_events.extract.dates_es import ParsedDateRange, find_date_ranges
from canarias_events.geo import municipalities_after_en
from canarias_events.http import mask_secrets
from canarias_events.models import SOCIAL_TRUST, Event
from canarias_events.sources.base import (
    FetchContext,
    SourceConfig,
    SourceFetchResult,
    resolve_island,
)
from canarias_events.sources.web import WebAdapter
from canarias_events.statuses import ADAPTER_OK, BLOCKED, NEEDS_MANUAL_REVIEW, RATE_LIMITED

logger = logging.getLogger(__name__)
LINK_PROBLEMS = frozenset({BLOCKED, RATE_LIMITED, NEEDS_MANUAL_REVIEW})
MAX_POSTS = 40
MAX_LINK_FETCHES = 8  # event pages fetched per run, however many posts there are
MAX_LINKS_PER_POST = 3
TITLE_CHARS = 140
RAW_TEXT_CHARS = 4096
_RE_URL = re.compile(r"https?://\S+")


class SocialParams(BaseModel):
    """``params`` of a ``mastodon`` / ``bluesky`` source (unknown keys are rejected)."""

    model_config = ConfigDict(extra="forbid")

    handle: str = Field(min_length=1)
    # Event pages are read only on these hosts (and their subdomains), chosen by a person.
    link_hosts: list[str] = Field(default_factory=list)
    # Posts rarely state the year; "infer" (opt-in) takes the one closest to the post's date.
    year_policy: Literal["strict", "infer"] = "strict"

    @classmethod
    def from_config(cls, cfg: SourceConfig) -> SocialParams:
        try:
            return cls.model_validate(cfg.params)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"]) or "params"
            raise ValueError(f"{cfg.id}: invalid params ({where}): {first['msg']}") from exc


def as_aware(moment: datetime | None) -> datetime | None:
    """Timestamps without an offset are UTC (feeds and APIs use it by convention)."""
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


@dataclass(frozen=True)
class SocialPost:
    id: str
    url: str | None
    text: str
    created_at: datetime | None
    links: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


def _clean(text: str) -> str:
    return " ".join(_RE_URL.sub(" ", text).split())


def _title(text: str) -> str:
    line = next((_clean(ln) for ln in text.splitlines() if _clean(ln)), "")
    if len(line) <= TITLE_CHARS:
        return line
    return line[:TITLE_CHARS].rsplit(" ", 1)[0].rstrip(",;:- ") + "…"


def _same_day(day: date, wanted: set[tuple[int, int, int | None]]) -> bool:
    return any(
        (day.month, day.day) == (month, d) and year in (None, day.year) for month, d, year in wanted
    )


def _moment(day: date, at: time | None) -> datetime | None:
    return datetime.combine(day, at, TZ) if at else None


class PostEvents:
    """Builds the events of one run; link fetches are bounded and cached."""

    def __init__(
        self,
        cfg: SourceConfig,
        ctx: FetchContext,
        result: SourceFetchResult,
        *,
        params: SocialParams,
        source_type: str,
        skip_hosts: Sequence[str],
    ) -> None:
        self.cfg, self.ctx, self.result = cfg, ctx, result
        self.params = params
        self.source_type = source_type
        self.link_hosts = {h.lower() for h in params.link_hosts}
        self.skip_hosts = {h.lower() for h in skip_hosts if h}
        self.fetches = 0
        self.pages: dict[str, list[Event]] = {}

    # ------------------------------------------------------------------ public

    def add_all(self, posts: Sequence[SocialPost]) -> None:
        for post in posts[:MAX_POSTS]:
            self.result.records_received += 1
            self.add(post)

    def add(self, post: SocialPost) -> None:
        reference = (as_aware(post.created_at) or self.ctx.now).astimezone(TZ).date()
        text = _clean(post.text)
        # The year is always resolved here; the strict policy is applied before a post's own
        # text is trusted (a linked page states its own, unambiguous date).
        ranges = find_date_ranges(text, reference_date=reference, year_policy="infer")
        if not ranges:
            self.result.discard("no deterministic date", post.id)
            return
        page_event = self._from_event_page(post, self._wanted_days(ranges))
        if page_event is not None:
            self.result.events.append(page_event)
            return
        distinct = {(r.start, r.end) for r in ranges}
        if len(distinct) > 1:
            self.result.discard("ambiguous: several dates", post.id)
            return
        if self.params.year_policy == "strict" and ranges[0].year_inferred:
            self.result.discard("year not stated (year_policy: strict)", post.id)
            return
        places = municipalities_after_en(text)
        if len(places) != 1:
            reason = "ambiguous: several places" if places else "no event page or place"
            self.result.discard(reason, post.id)
            return
        self._from_text(post, ranges[0], next(iter(places)))

    # ------------------------------------------------------------------ event pages

    def _page_events(self, link: str) -> list[Event]:
        if link in self.pages:
            return self.pages[link]
        found: list[Event] = []
        self.pages[link] = found
        if self.fetches >= MAX_LINK_FETCHES:
            return found
        self.fetches += 1
        try:
            page_cfg = SourceConfig(
                id=f"{self.cfg.id}:page", name=self.cfg.name, adapter="web", url=link,
                params={"jsonld_only": True},
            )  # fmt: skip
            sub = WebAdapter(page_cfg).fetch(self.ctx)
        except Exception as exc:  # isolation: one bad link must not cost the other posts
            logger.warning("link %s skipped: %s", mask_secrets(link), exc)
            self.result.warnings.append(f"link skipped: {mask_secrets(link)}: {exc}")
            return found
        for warning in sub.warnings:
            if "robots.txt" in warning:
                self.result.warnings.append(f"link: {warning}")
        if sub.status == ADAPTER_OK:
            found.extend(sub.events)
        elif sub.status in LINK_PROBLEMS:
            host = urlsplit(link).hostname or "?"
            self.result.warnings.append(f"link skipped: {host}: {sub.status}")
        return found

    def _wanted_days(self, ranges: Sequence[ParsedDateRange]) -> set[tuple[int, int, int | None]]:
        """(month, day, year) a linked page may match; year None: the post did not state it.

        Under the strict policy a post that states no year never adopts the page's year.
        """
        strict = self.params.year_policy == "strict"
        return {
            (r.start.month, r.start.day, None if r.year_inferred else r.start.year)
            for r in ranges
            if not (strict and r.year_inferred)
        }

    def _linkable(self, host: str) -> bool:
        if host in self.skip_hosts:
            return False
        return any(host == allowed or host.endswith(f".{allowed}") for allowed in self.link_hosts)

    def _links(self, post: SocialPost) -> list[str]:
        links: list[str] = []
        for link in post.links:
            host = (urlsplit(link).hostname or "").lower()
            if host and self._linkable(host) and link not in links:
                links.append(link)
        return links[:MAX_LINKS_PER_POST]

    def _from_event_page(
        self, post: SocialPost, days: set[tuple[int, int, int | None]]
    ) -> Event | None:
        if not days:
            return None
        for link in self._links(post):
            matching = [
                e
                for e in self._page_events(link)
                if e.start_date is not None and _same_day(e.start_date, days)
            ]
            if len(matching) != 1:
                continue  # none, or several events on that date: not an unambiguous match
            event = matching[0]
            url = event.official_event_url or link  # the page that was fetched
            extra = {**event.extra, **self._extra(post, "event_page")}
            return event.model_copy(
                update={
                    "id": None, "extra": extra, "raw_data": self._raw(post),
                    "official_event_url": url,
                    "source_name": self.cfg.name, "source_url": self.cfg.url,
                    "source_event_id": post.id, "source_type": self.source_type,
                    "source_license": self.cfg.license,
                }
            )  # fmt: skip
        return None

    # ------------------------------------------------------------------ post text

    def _from_text(self, post: SocialPost, parsed: ParsedDateRange, municipality: str) -> None:
        start_dt = _moment(parsed.start, parsed.start_time)
        end_day = parsed.end or (parsed.start if parsed.end_time else None)
        end_dt = _moment(end_day, parsed.end_time) if start_dt and end_day else None
        extra = self._extra(post, "post_text")
        extra["year_inferred"] = parsed.year_inferred
        self.result.add_event(
            post.id,
            title=_title(post.text),
            start_datetime=start_dt,
            end_datetime=end_dt,
            start_date=None if start_dt else parsed.start,
            end_date=None if start_dt else parsed.end,
            all_day=start_dt is None,
            municipality=municipality,
            island=resolve_island(self.cfg, municipality=municipality),
            source_name=self.cfg.name,
            source_url=self.cfg.url,
            source_event_id=post.id,
            source_type=self.source_type,
            source_license=self.cfg.license,
            raw_data=self._raw(post),
            extra=extra,
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _extra(post: SocialPost, derived_from: str) -> dict[str, Any]:
        return {
            "source_trust": SOCIAL_TRUST, "post_url": post.url, "derived_from": derived_from,
        }  # fmt: skip

    @staticmethod
    def _raw(post: SocialPost) -> dict[str, Any]:
        return {
            "post": {
                "id": post.id, "url": post.url, "text": post.text[:RAW_TEXT_CHARS],
                "created_at": post.created_at.isoformat() if post.created_at else None,
            }
        }  # fmt: skip
