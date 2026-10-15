"""Tiered acquisition from HTML portals: JSON-LD, sitemap, embedded JSON, blocks, renderer, model.

Tiers run in order and the first one that produces events wins:

1. JSON-LD / microdata on the listing page(s);
2. sitemap URLs (only those matching ``sitemap_url_pattern``) -> detail-page JSON-LD;
3. JSON embedded in the page (Next/Nuxt payloads, Livewire snapshots, data attributes);
4. generic repeated blocks on the static HTML;
5. the optional JS renderer (``render``), then tiers 1, 3 and 4 again on the rendered DOM;
6. the optional local model (global ``ai.enabled`` AND ``ai: true`` on the source);
7. otherwise the source is marked ``needs_manual_review`` with diagnostics.

Everything goes through ``SafeHttpClient`` and ``robots.txt``. A 401/403/429 or a challenge page
stops the crawl with status ``blocked``; nothing tries to get around it.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time as clock
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lxml import etree
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from canarias_events.extract.blocks import BlockHints, extract_blocks
from canarias_events.extract.candidate import CandidateEvent, dedupe
from canarias_events.extract.embedded import extract_embedded
from canarias_events.extract.guards import (
    ContentTypeRejected,
    check_html_content_type,
    is_challenge_page,
    looks_js_driven,
)
from canarias_events.extract.jsonld import extract_jsonld
from canarias_events.extract.llm import LlmError, OllamaExtractor
from canarias_events.extract.renderers import (
    Renderer,
    RenderError,
    RendererUnavailable,
    make_renderer,
)
from canarias_events.extract.robots import RobotsPolicy
from canarias_events.extract.text import html_to_markdownish
from canarias_events.geo import normalize_municipality
from canarias_events.http import FetchError, HttpError, InvalidPayload, mask_secrets
from canarias_events.normalize import canonical_url, normalize_title
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceConfig,
    SourceFetchResult,
    resolve_island,
)

logger = logging.getLogger("canarias_events.web")

MAX_SITEMAP_URLS = 50_000
LLM_MAX_PAGES = 3
BLOCKING_STATUSES = frozenset({401, 403, 429})
_RE_URL_MONTH = re.compile(r"(?<!\d)(20\d{2})[/-](0[1-9]|1[0-2])(?!\d)")
_RE_PAREN = re.compile(r"\(([^()]+)\)\s*$")
_RE_CHARSET = re.compile(r"charset=([\w-]+)", re.IGNORECASE)

RendererFactory = Callable[[str], Renderer]
ExtractorFactory = Callable[[Any], OllamaExtractor]


# --------------------------------------------------------------------------- configuration


class HintsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_selector: str | None = None
    date_selector: str | None = None
    title_selector: str | None = None


class WebParams(BaseModel):
    """``params`` of a ``web`` source in ``sources.yaml`` (unknown keys are rejected)."""

    model_config = ConfigDict(extra="forbid")

    listing_urls: list[str] = Field(default_factory=list)
    sitemap_url: str | None = None
    sitemap_url_pattern: str | None = None
    max_detail_pages: int = Field(default=40, ge=0, le=500)
    detail_url_pattern: str | None = None
    follow_detail_pages: bool = False
    render: Literal["none", "playwright", "crawl4ai", "auto"] = "none"
    render_timeout_ms: int = Field(default=20_000, ge=1000, le=120_000)
    hints: HintsParams | None = None
    year_policy: Literal["strict", "infer"] = "strict"
    max_pages_per_run: int = Field(default=60, ge=1, le=1000)
    timezone: str = "Atlantic/Canary"
    ai: bool = False
    empty_marker: str | None = None
    venue_name: str | None = None

    @field_validator("sitemap_url_pattern", "detail_url_pattern", "empty_marker")
    @classmethod
    def _valid_regex(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regular expression: {exc}") from exc
        return value

    @field_validator("listing_urls", "sitemap_url")
    @classmethod
    def _http_urls(cls, value: Any) -> Any:
        for url in value if isinstance(value, list) else [value]:
            if url is not None and urlsplit(url).scheme not in ("http", "https"):
                raise ValueError(f"must be http(s): {url}")
        return value

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

    @model_validator(mode="after")
    def _sitemap_needs_a_pattern(self) -> WebParams:
        if self.sitemap_url and not self.sitemap_url_pattern:
            raise ValueError("sitemap_url requires sitemap_url_pattern")
        return self


# --------------------------------------------------------------------------- run state


@dataclass
class _Page:
    url: str
    html: str
    raw: bytes = b""


class _Blocked(Exception):
    """401/403/429 or a challenge page: the crawl stops (never bypassed)."""


class _RobotsDisallowed(Exception):
    pass


class _PageLimit(Exception):
    pass


def _decode(content: bytes, headers: dict[str, str]) -> str:
    declared = _RE_CHARSET.search(headers.get("content-type", ""))
    for encoding in ([declared.group(1)] if declared else []) + ["utf-8"]:
        try:
            return content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("latin-1", errors="replace")


def _url_month(url: str) -> tuple[int, int] | None:
    found = _RE_URL_MONTH.search(urlsplit(url).path)
    return (int(found.group(1)), int(found.group(2))) if found else None


def _parse_sitemap(content: bytes) -> tuple[list[str], str | None]:
    """URLs of a sitemap, or ``([], reason)``. XML entities and network access are disabled."""
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, recover=False
    )
    try:
        root = etree.fromstring(content, parser)
    except (etree.XMLSyntaxError, ValueError):
        return [], "malformed sitemap"
    name = etree.QName(root).localname
    if name == "sitemapindex":
        return [], "sitemap index is not followed (point sitemap_url at a child sitemap)"
    urls = [
        (el.text or "").strip()
        for el in root.iter()
        if isinstance(el.tag, str) and etree.QName(el).localname == "loc"
    ]
    return [u for u in urls if u][:MAX_SITEMAP_URLS], None


class _Run:
    def __init__(self, adapter: WebAdapter, ctx: FetchContext) -> None:
        self.adapter = adapter
        self.cfg = adapter.config
        self.p = adapter.params
        self.ctx = ctx
        self.result = SourceFetchResult()
        self.robots = RobotsPolicy(ctx.client)
        self.fetched = 0
        self.cache: dict[str, _Page] = {}
        self.renderers: list[Renderer] = []
        self.block_reason: str | None = None
        self.limit_warned = False
        self.reference = ctx.now.date()
        self.hosts = self._allowed_hosts()
        self.hints = BlockHints(**self.p.hints.model_dump()) if self.p.hints is not None else None

    # ------------------------------------------------------------------ helpers

    def _allowed_hosts(self) -> set[str]:
        hosts: set[str] = set()
        for url in [self.cfg.url, *self.p.listing_urls, self.p.sitemap_url or ""]:
            host = urlsplit(url).hostname if url else None
            if host:
                bare = host.removeprefix("www.")
                hosts.update({host, bare, f"www.{bare}"})
        return hosts

    def warn(self, text: str) -> None:
        if text not in self.result.warnings:
            self.result.warnings.append(text)

    def close(self) -> None:
        for renderer in self.renderers:
            try:
                renderer.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                logger.debug("renderer close failed", exc_info=True)

    # ------------------------------------------------------------------ fetching

    def fetch(self, url: str, *, html: bool = True) -> _Page:
        cached = self.cache.get(url)
        if cached is not None:
            return cached
        if self.fetched >= self.p.max_pages_per_run:
            if not self.limit_warned:
                self.limit_warned = True
                self.warn(f"max_pages_per_run={self.p.max_pages_per_run} reached; fetching stopped")
            raise _PageLimit
        if not self.robots.allowed(url):
            state = self.robots.status(url)
            reason = "robots.txt unavailable" if state == "unavailable" else "robots.txt disallows"
            self.warn(f"{reason}: {mask_secrets(url)} not fetched")
            raise _RobotsDisallowed(url)
        self.fetched += 1
        self.result.pages += 1
        try:
            response = self.ctx.client.get(url, allowed_hosts=self.hosts)
        except HttpError as exc:
            if exc.status in BLOCKING_STATUSES:
                raise _Blocked(f"HTTP {exc.status} for {mask_secrets(url)}") from exc
            raise
        if html:
            try:
                check_html_content_type(response.headers)
            except ContentTypeRejected as exc:
                raise InvalidPayload(f"{mask_secrets(url)}: {exc}") from exc
        text = _decode(response.content, response.headers)
        looks_html = html or "html" in response.headers.get("content-type", "").lower()
        if looks_html and is_challenge_page(text):
            raise _Blocked(f"challenge page at {mask_secrets(url)}")
        page = _Page(response.url, text, response.content)
        self.cache[url] = page
        return page

    def fetch_extra(self, url: str, *, html: bool = True) -> _Page | None:
        """Fetch a secondary page; its failures are warnings, a block stops the crawl."""
        if self.block_reason is not None:
            return None
        try:
            return self.fetch(url, html=html)
        except _Blocked as exc:
            self.block_reason = str(exc)
        except (_RobotsDisallowed, _PageLimit):
            pass
        except FetchError as exc:
            self.warn(f"page failed: {exc}")
        return None

    def load_listing(self) -> list[_Page]:
        pages: list[_Page] = []
        for index, url in enumerate(self.p.listing_urls or [self.cfg.url]):
            if index == 0:
                pages.append(self.fetch(url))  # failures of the first page decide the status
                continue
            extra = self.fetch_extra(url)
            if extra is not None:
                pages.append(extra)
        return pages

    # ------------------------------------------------------------------ tiers

    def tier_jsonld(self, pages: list[_Page]) -> list[CandidateEvent]:
        found: list[CandidateEvent] = []
        for page in pages:
            found.extend(extract_jsonld(page.html, page.url, include_microdata=True).candidates)
        return found

    def tier_embedded(self, pages: list[_Page]) -> list[CandidateEvent]:
        found: list[CandidateEvent] = []
        for page in pages:
            found.extend(
                extract_embedded(
                    page.html,
                    page.url,
                    reference_date=self.reference,
                    year_policy=self.p.year_policy,
                ).candidates
            )
        return found

    def tier_blocks(self, pages: list[_Page]) -> list[CandidateEvent]:
        found: list[CandidateEvent] = []
        for page in pages:
            blocks = extract_blocks(
                page.html,
                page.url,
                detail_url_pattern=self.p.detail_url_pattern,
                hints=self.hints,
                reference_date=self.reference,
                year_policy=self.p.year_policy,
            )
            for text in blocks.warnings:
                self.warn(text)
            found.extend(blocks.candidates)
        return found

    def sitemap_urls(self) -> list[str]:
        assert self.p.sitemap_url and self.p.sitemap_url_pattern
        page = self.fetch_extra(self.p.sitemap_url, html=False)
        if page is None:
            return []
        urls, problem = _parse_sitemap(page.raw)
        if problem:
            self.warn(f"{problem}: {mask_secrets(self.p.sitemap_url)}")
            return []
        pattern = re.compile(self.p.sitemap_url_pattern)
        window = self.ctx.window
        wanted: set[tuple[int, int]] = set()
        day = window.start_date
        while day <= window.end_date_inclusive:
            wanted.add((day.year, day.month))
            day = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
        first_year, first_month = window.start_date.year, window.start_date.month
        previous = (first_year - (first_month == 1), (first_month - 2) % 12 + 1)
        ranked: list[tuple[int, str]] = []
        for url in urls:
            host = urlsplit(url).hostname or ""
            if host not in self.hosts or not pattern.search(url):
                continue
            month = _url_month(url)
            if month is None:
                ranked.append((1, url))  # undated URL: cannot be ruled out
            elif month in wanted:
                ranked.append((0, url))
            elif month == previous:
                ranked.append((1, url))
        ranked.sort(key=lambda item: item[0])  # stable: sitemap order inside each class
        return [url for _, url in ranked][: self.p.max_detail_pages]

    def tier_detail(self) -> list[CandidateEvent] | None:
        if not self.p.sitemap_url:
            return None
        found: list[CandidateEvent] = []
        for url in self.sitemap_urls():
            page = self.fetch_extra(url)
            if page is None:
                if self.block_reason is not None:
                    break
                continue
            found.extend(
                extract_jsonld(
                    page.html, page.url, url_fallback=True, include_microdata=True
                ).candidates
            )
        return found

    def tier_llm(self, pages: list[_Page]) -> list[CandidateEvent] | None:
        settings = self.ctx.ai
        if not self.p.ai or settings is None or not settings.enabled:
            return None
        extractor = self.adapter.extractor_factory(settings)
        found: list[CandidateEvent] = []
        for page in pages[:LLM_MAX_PAGES]:
            text = html_to_markdownish(page.html, page.url, max_chars=settings.max_input_chars)
            if not text:
                continue
            try:
                found.extend(extractor.extract(text, page.url, reference_date=self.reference))
            except LlmError as exc:
                self.warn(f"ollama: {exc}")
                break
        return found

    # ------------------------------------------------------------------ rendering

    def render_pages(self, pages: list[_Page]) -> list[_Page]:
        kinds = ["playwright", "crawl4ai"] if self.p.render == "auto" else [self.p.render]
        for kind in kinds:
            try:
                renderer = self.adapter.renderer_factory(kind)
            except RendererUnavailable as exc:
                self.warn(f"render tier skipped ({kind}): {exc}")
                continue
            self.renderers.append(renderer)
            rendered: list[_Page] = []
            unavailable = False
            for page in pages:
                if not self.robots.allowed(page.url):  # the final URL after redirects may differ
                    self.warn(f"robots.txt disallows rendering {mask_secrets(page.url)}")
                    continue
                try:
                    out = renderer.render(page.url)
                except RendererUnavailable as exc:
                    self.warn(f"render tier skipped ({kind}): {exc}")
                    unavailable = True
                    break
                except RenderError as exc:
                    if exc.status in BLOCKING_STATUSES:
                        self.block_reason = (
                            f"HTTP {exc.status} while rendering {mask_secrets(page.url)}"
                        )
                        return rendered
                    self.warn(f"render failed for {mask_secrets(page.url)}: {exc}")
                    continue
                except FetchError as exc:
                    self.warn(f"render failed for {mask_secrets(page.url)}: {exc}")
                    continue
                if is_challenge_page(out.html):
                    self.block_reason = f"challenge page after rendering {mask_secrets(page.url)}"
                    return rendered
                rendered.append(_Page(out.final_url, out.html))
            if not unavailable:
                return rendered
        return []

    # ------------------------------------------------------------------ cascade

    def attempt(
        self, tier: str, produce: Callable[[], list[CandidateEvent] | None]
    ) -> list[CandidateEvent]:
        began = clock.monotonic()
        found = produce()
        if found is None:
            return []
        found = dedupe(found)
        self.result.tiers.append(
            {"tier": tier, "candidates": len(found), "seconds": round(clock.monotonic() - began, 3)}
        )
        return found

    def static_tiers(
        self, pages: list[_Page], prefix: str = ""
    ) -> tuple[str, list[CandidateEvent]]:
        steps: list[tuple[str, Callable[[], list[CandidateEvent] | None]]] = [
            ("jsonld", lambda: self.tier_jsonld(pages)),
        ]
        if not prefix:
            steps.append(("detail-jsonld", self.tier_detail))
        steps.extend(
            [
                ("embedded", lambda: self.tier_embedded(pages)),
                ("blocks", lambda: self.tier_blocks(pages)),
            ]
        )
        for name, produce in steps:
            tier = f"{prefix}{name}"
            found = self.attempt(tier, produce)
            if found:
                return tier, found
        return "", []

    def empty_marker_hit(self, pages: list[_Page]) -> bool:
        if not self.p.empty_marker:
            return False
        marker = re.compile(self.p.empty_marker, re.IGNORECASE)
        return any(
            marker.search(html_to_markdownish(p.html, p.url, max_chars=100_000)) for p in pages
        )

    def note_listing(self, pages: list[_Page]) -> None:
        """Content hash (health history) and raw first page (optional diagnostic snapshot)."""
        text = "\n".join(html_to_markdownish(p.html, p.url, max_chars=200_000) for p in pages)
        self.result.listing_hash = hashlib.sha256(" ".join(text.split()).encode()).hexdigest()
        self.result.snapshot = pages[0].html if pages else None

    def render_wanted(self, pages: list[_Page]) -> bool:
        if self.p.render == "none":
            return False
        if self.p.render == "auto":
            return any(looks_js_driven(page.html) for page in pages)
        return True

    def execute(self) -> SourceFetchResult:
        result = self.result
        result.extraction_ok = False
        try:
            pages = self.load_listing()
        except _Blocked as exc:
            return self.finish_blocked(str(exc))
        except _RobotsDisallowed:
            result.status = "robots_disallowed"
            result.warnings.append("source skipped: robots.txt does not allow fetching the listing")
            return result
        except _PageLimit:  # pragma: no cover - max_pages_per_run >= 1
            return result
        self.note_listing(pages)
        tier, found = self.static_tiers(pages)
        if not found and self.block_reason is None:
            if self.empty_marker_hit(pages):
                result.extraction_ok, result.strategy_used = True, "empty-marker"
                return result
            if self.render_wanted(pages):
                tier, found = self.render_cascade(pages)
        if not found and self.block_reason is None:
            llm_pages = list(pages)
            began = clock.monotonic()
            found = self.tier_llm(llm_pages) or []
            if self.p.ai and self.ctx.ai is not None and self.ctx.ai.enabled:
                result.tiers.append(
                    {
                        "tier": "llm",
                        "candidates": len(found),
                        "seconds": round(clock.monotonic() - began, 3),
                    }
                )
            if found:
                tier = "llm"
        if self.block_reason is not None:
            result.warnings.append(f"blocked: {self.block_reason} (not bypassed)")
            result.status = "blocked"
        if not found:
            if self.block_reason is None:
                result.status = "needs_manual_review"
                tried = ", ".join(str(t["tier"]) for t in result.tiers)
                result.warnings.append(
                    f"needs manual review: no extraction strategy produced events (tried: {tried})"
                )
            return result
        self.build_events(found, tier)
        return result

    def render_cascade(self, pages: list[_Page]) -> tuple[str, list[CandidateEvent]]:
        began = clock.monotonic()
        rendered = self.render_pages(pages)
        self.result.tiers.append(
            {
                "tier": "render",
                "candidates": 0,
                "seconds": round(clock.monotonic() - began, 3),
                "pages": len(rendered),
            }
        )
        if not rendered:
            return "", []
        return self.static_tiers(rendered, prefix="render+")

    def finish_blocked(self, reason: str) -> SourceFetchResult:
        self.result.status = "blocked"
        self.result.extraction_ok = False
        self.result.warnings.append(f"blocked: {reason} (not bypassed)")
        return self.result

    # ------------------------------------------------------------------ events

    def build_events(self, found: list[CandidateEvent], tier: str) -> None:
        result = self.result
        found = dedupe(found)
        self.enrich(found)
        result.strategy_used = tier
        result.extraction_ok = True
        result.records_received = len(found)
        for cand in found:
            self.add_event(cand, tier)

    def enrich(self, found: list[CandidateEvent]) -> None:
        """Fill gaps from detail-page JSON-LD (opt-in; never overrides or contradicts listings)."""
        if not self.p.follow_detail_pages or self.p.max_detail_pages == 0:
            return
        window = self.ctx.window
        budget = self.p.max_detail_pages
        for cand in found:
            if budget <= 0 or self.block_reason is not None:
                break
            first, last = cand.first_day, cand.last_day or cand.first_day
            if (
                not cand.official_event_url
                or (urlsplit(cand.official_event_url).hostname or "") not in self.hosts
                or first is None
                or last is None
                or first > window.end_date_inclusive
                or last < window.start_date
                or not (
                    cand.venue_name is None
                    or cand.start_datetime is None
                    or cand.description is None
                )
            ):
                continue
            budget -= 1
            page = self.fetch_extra(cand.official_event_url)
            if page is None:
                continue
            detail = extract_jsonld(page.html, page.url, include_microdata=True).candidates
            match = next((d for d in detail if d.first_day == first), None)
            if match is not None:
                _merge(cand, match)

    def add_event(self, cand: CandidateEvent, tier: str) -> None:
        cfg = self.cfg
        venue = cand.venue_name or self.p.venue_name
        municipality = (
            cand.municipality
            or _municipality_from_venue(venue)
            or normalize_municipality(cfg.municipality)
        )
        key = (
            canonical_url(cand.official_event_url)
            if cand.official_event_url
            else normalize_title(cand.title)
        )
        digest = hashlib.sha1(f"{key}|{cand.first_day}".encode()).hexdigest()[:16]
        extra: dict[str, Any] = dict(cand.extra)
        extra["extraction"] = {
            "strategy": tier,
            "confidence": cand.confidence,
            "year_inferred": cand.year_inferred,
        }
        self.result.add_event(
            digest,
            title=cand.title,
            description=cand.description,
            category=cand.category,
            start_datetime=cand.start_datetime,
            end_datetime=cand.end_datetime,
            start_date=cand.start_date,
            end_date=cand.end_date,
            timezone=self.p.timezone,
            all_day=cand.start_datetime is None,
            venue_name=venue,
            address=cand.address,
            municipality=municipality,
            island=resolve_island(
                cfg,
                municipality=municipality,
                latitude=cand.latitude,
                longitude=cand.longitude,
            ),
            postal_code=cand.postal_code,
            latitude=cand.latitude,
            longitude=cand.longitude,
            price_min=cand.price_min,
            price_max=cand.price_max,
            currency=cand.currency,
            is_free=cand.is_free,
            ticket_url=cand.ticket_url,
            official_event_url=cand.official_event_url,
            image_url=cand.image_url,
            organizer_name=cand.organizer_name,
            organizer_url=cand.organizer_url,
            source_name=cfg.name,
            source_url=cfg.url,
            source_event_id=f"h:{digest}",
            source_type=WebAdapter.source_type,
            source_license=cfg.license,
            raw_data=cand.raw,
            extra=extra,
        )


_MERGE_FIELDS = (
    "description", "category", "venue_name", "address", "municipality", "postal_code", "latitude",
    "longitude", "price_min", "price_max", "currency", "is_free", "ticket_url", "image_url",
    "organizer_name", "organizer_url",
)  # fmt: skip


def _merge(target: CandidateEvent, detail: CandidateEvent) -> None:
    changed = False
    for name in _MERGE_FIELDS:
        if getattr(target, name) is None and getattr(detail, name) is not None:
            setattr(target, name, getattr(detail, name))
            changed = True
    if (
        target.start_datetime is None
        and detail.start_datetime is not None
        and target.start_date == detail.start_datetime.date()
    ):
        target.start_datetime, target.start_date = detail.start_datetime, None
        target.end_datetime = target.end_datetime or detail.end_datetime
        changed = True
    if changed:
        target.extra["enriched_from"] = "detail-jsonld"


def _municipality_from_venue(venue: str | None) -> str | None:
    """``"Valleseco (Valleseco)"`` style venues name their municipality in parentheses."""
    if not venue:
        return None
    found = _RE_PAREN.search(venue)
    return normalize_municipality(found.group(1)) if found else None


class WebAdapter(SourceAdapter):
    source_type = "html"

    def __init__(
        self,
        config: SourceConfig,
        *,
        renderer_factory: RendererFactory | None = None,
        extractor_factory: ExtractorFactory | None = None,
    ) -> None:
        super().__init__(config)
        try:
            self.params = WebParams.model_validate(config.params)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"]) or "params"
            raise ValueError(f"{config.id}: invalid web params ({where}): {first['msg']}") from exc
        self.renderer_factory: RendererFactory = renderer_factory or self._default_renderer
        self.extractor_factory: ExtractorFactory = extractor_factory or OllamaExtractor

    def _default_renderer(self, kind: str) -> Renderer:
        return make_renderer(kind, page_timeout_ms=self.params.render_timeout_ms)

    def fetch(self, ctx: FetchContext) -> SourceFetchResult:
        run = _Run(self, ctx)
        try:
            return run.execute()
        finally:
            run.close()


__all__ = ["HintsParams", "WebAdapter", "WebParams"]
