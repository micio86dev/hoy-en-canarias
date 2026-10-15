"""Generic, layout-resilient extraction of repeated event blocks from static HTML.

No per-site CSS is required: repeated sibling-like elements that each carry a link and a date
are read as event cards. Optional selector ``hints`` are tried first and fall back to the
generic detection (with a ``selector_drift`` warning) when the site layout changes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urlsplit

from lxml.cssselect import CSSSelector

from canarias_events.date_range import TZ
from canarias_events.extract.candidate import CandidateEvent, dedupe, fragment, resolve_url
from canarias_events.extract.dates_es import (
    ParsedDateRange,
    YearPolicy,
    find_date_ranges,
    mentions_date_without_year,
    parse_date_range,
    parse_time_range,
)
from canarias_events.extract.util import parse_html
from canarias_events.normalize import parse_datetime_any, parse_price

MAX_MEMBER_CHARS = 20_000
MAX_GROUPS = 80
MAX_MEMBERS = 2500
MAX_CANDIDATES = 2000
MAX_DEPTH = 200
MIN_GROUP = 3
RAW_CHARS = 4096

_DROP_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "canvas", "select")
_DROP_CLASSES = frozenset(
    {"nav", "navbar", "navigation", "menu", "main-menu", "breadcrumb", "breadcrumbs", "cookies",
     "cookie-banner", "skip-link", "megamenu"}
)  # fmt: skip
_BLOCK_TAGS = frozenset(
    {"div", "p", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
     "header", "footer", "figure", "figcaption", "table", "tr", "td", "th", "dl", "dt", "dd",
     "blockquote", "br", "address", "main", "aside", "form", "details", "summary"}
)  # fmt: skip
_NOISE_CLASSES = frozenset(
    {"first", "last", "odd", "even", "par", "impar", "active", "current", "selected", "featured",
     "hidden", "show", "open", "in", "collapse", "clearfix"}
)  # fmt: skip
_SKIP_MEMBER_TAGS = frozenset(
    {"html", "body", "head", "br", "img", "option", "script", "style", "hr", "input", "p"}
)  # "p" alone is too small to be a card; paragraph grids are rare and noisy

_DATE_TOKENS = frozenset(
    {"date", "dates", "fecha", "fechas", "dia", "mes", "day", "month", "when", "cuando",
     "calendar", "calendario", "datetime", "hora", "horario", "schedule"}
)  # fmt: skip
_PUB_TOKENS = frozenset(
    {"published", "publicado", "posted", "publish", "pubdate", "byline", "updated", "modified",
     "created", "publicacion"}
)  # fmt: skip
_TITLE_TOKENS = frozenset(
    {"title", "titulo", "name", "nombre", "titular", "headline", "heading", "nom"}
)
_VENUE_TOKENS = frozenset(
    {"lugar", "venue", "location", "place", "ubicacion", "recinto", "where", "sitio"}
)
_CATEGORY_TOKENS = frozenset(
    {"categoria", "categorias", "category", "categories", "genre", "genero"}
)
_PRICE_TOKENS = frozenset({"price", "precio", "cost", "coste", "tarifa", "entrada", "entradas"})
_DESCRIPTION_TOKENS = frozenset(
    {"descripcion", "description", "resumen", "excerpt", "summary", "extracto", "entradilla"}
)
_NEWS_SEGMENTS = frozenset(
    {"noticia", "noticias", "news", "prensa", "blog", "articulo", "articulos", "post", "posts",
     "nota-de-prensa", "notas-de-prensa", "notas"}
)  # fmt: skip
_GENERIC_TITLES = frozenset(
    {"leer mas", "mas info", "mas informacion", "ver mas", "saber mas", "read more", "more",
     "info", "ir al detalle", "detalle", "entradas", "comprar", "comprar entradas", "tickets",
     "+ info", "ver", "aqui", "click aqui", "compartir", "share", "ver detalle", "ver evento",
     "mas", "+", "programa", "ver programa", "reservar", "inscribete", "inscripcion"}
)  # fmt: skip
_ASSET_EXT = re.compile(
    r"\.(?:jpe?g|png|gif|webp|svg|pdf|docx?|xlsx?|pptx?|zip|rar|mp[34]|css|js|ico|xml|json)$",
    re.IGNORECASE,
)
_VENUE_LABEL = re.compile(
    r"\b(?:lugar(?: de celebraci[oó]n)?|d[oó]nde|ubicaci[oó]n|venue|location|recinto|place)"
    r"\s*[:：]\s*([^\n]+)",
    re.IGNORECASE,
)
_PRICE_SNIPPET = re.compile(
    r"(?:(?:desde|from|a partir de)\s+)?(?:€\s*\d+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?\s*€)",
    re.IGNORECASE,
)
_FREE_WORDS = re.compile(
    r"^(?:gratis|gratuito|gratuita|entrada libre|entrada gratuita|acceso libre|"
    r"free(?: entry)?)\.?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BlockHints:
    """Optional CSS selectors from the source configuration, tried before generic detection."""

    block_selector: str | None = None
    date_selector: str | None = None
    title_selector: str | None = None


@dataclass
class BlocksResult:
    candidates: list[CandidateEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    groups: int = 0
    blocks_seen: int = 0
    undated: int = 0
    news_like: int = 0
    year_missing: int = 0
    selector_drift: bool = False
    recognized: bool = False


# --------------------------------------------------------------------------- tiny helpers


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def _tokens(el: Any) -> set[str]:
    raw = f"{el.get('class') or ''} {el.get('id') or ''}"
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", raw)
    return set(re.findall(r"[a-z]+", spaced.lower()))


def _signature(el: Any) -> tuple[str, tuple[str, ...]]:
    classes = sorted(
        c
        for c in (el.get("class") or "").split()
        if c.lower() not in _NOISE_CLASSES and not re.search(r"\d", c)
    )
    return el.tag, tuple(classes)


def _block_text(el: Any, depth: int = 0) -> str:
    """Rendered-like text: block elements and ``<br>`` break lines, inline elements do not."""
    parts: list[str] = []

    def walk(node: Any, level: int) -> None:
        if level > MAX_DEPTH or not isinstance(node.tag, str):
            return
        tag = node.tag
        if tag in _DROP_TAGS:
            return
        block = tag in _BLOCK_TAGS
        if block:
            parts.append("\n")
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child, level + 1)
            if child.tail:
                parts.append(child.tail)
        if block:
            parts.append("\n")

    walk(el, depth)
    text = "".join(parts).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _one_line(el: Any) -> str:
    return " ".join(_block_text(el).split())


def _registrable(host: str | None) -> str:
    if not host:
        return ""
    parts = host.lower().removeprefix("www.").split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else ".".join(parts)


def _is_generic(text: str) -> bool:
    return _fold(text).strip(" .:·|-»>") in _GENERIC_TITLES


# --------------------------------------------------------------------------- pruning


def _prune(root: Any) -> None:
    for el in list(root.iter(*_DROP_TAGS, "nav")):
        el.drop_tree()
    for el in list(root.iter("header", "footer")):
        if not any(a.tag in ("article", "li", "main", "section") for a in el.iterancestors()):
            el.drop_tree()
    for el in list(root.iter("form")):
        if len(el.text_content()) < 800:
            el.drop_tree()
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        classes = set((el.get("class") or "").lower().split())
        role = (el.get("role") or "").lower()
        if classes & _DROP_CLASSES or role in ("navigation", "banner", "contentinfo"):
            if el.getparent() is not None:
                el.drop_tree()


# --------------------------------------------------------------------------- member reading


@dataclass
class _Ctx:
    base: str | None
    pattern: re.Pattern[str] | None
    reference: date | None
    policy: YearPolicy
    context_year: int | None
    hints: BlockHints | None = None
    title_sel: Any = None
    date_sel: Any = None


@dataclass
class _Read:
    status: str  # ok | news | undated | yearless | untitled
    candidate: CandidateEvent | None = None
    ranges: int = 0
    hint_miss: bool = False


def _anchors(el: Any) -> list[Any]:
    found = [el] if el.tag == "a" and el.get("href") else []
    found.extend(a for a in el.iter("a") if a.get("href"))
    return found


def _detail_url(el: Any, title_el: Any, ctx: _Ctx) -> str | None:
    base_host = _registrable(urlsplit(ctx.base).hostname) if ctx.base else ""
    base_clean = (ctx.base or "").split("#", 1)[0]
    valid: list[tuple[Any, str]] = []
    for anchor in _anchors(el):
        url = resolve_url(ctx.base, anchor.get("href"))
        if url is None or url.split("#", 1)[0] == base_clean:
            continue
        parts = urlsplit(url)
        if ctx.pattern is not None:
            if not ctx.pattern.search(url):
                continue
        else:
            if _registrable(parts.hostname) != base_host and base_host:
                continue
            if parts.path in ("", "/") and not parts.query:
                continue
        if _ASSET_EXT.search(parts.path):
            continue
        valid.append((anchor, url))
    if not valid:
        return None
    if title_el is not None:
        for anchor, url in valid:
            if (
                anchor is title_el
                or title_el in anchor.iterdescendants()
                or anchor in title_el.iterdescendants()
            ):
                return url
    for anchor, url in valid:
        if not _is_generic(_one_line(anchor)) and _one_line(anchor):
            return url
    return valid[0][1]


def _find_title(el: Any, ctx: _Ctx) -> tuple[str | None, Any]:
    if ctx.title_sel is not None:
        for hit in ctx.title_sel(el):
            text = _one_line(hit)
            if text and not _is_generic(text):
                return text, hit
    for heading in el.iter("h1", "h2", "h3", "h4", "h5", "h6"):
        text = _one_line(heading)
        if 2 <= len(text) <= 300 and not _is_generic(text):
            return text, heading
    for node in el.iter():
        if not isinstance(node.tag, str) or node is el:
            continue
        if _tokens(node) & _TITLE_TOKENS:
            text = _one_line(node)
            if 2 <= len(text) <= 300 and not _is_generic(text):
                return text, node
    for anchor in _anchors(el):
        text = _one_line(anchor)
        if 3 <= len(text) <= 300 and not _is_generic(text):
            return text, anchor
        label = " ".join((anchor.get("title") or "").split())
        if 3 <= len(label) <= 300 and not _is_generic(label):
            return label, anchor
    return None, None


def _date_texts(el: Any, ctx: _Ctx) -> tuple[list[str], bool]:
    """Texts that may hold the date (best first) and whether they look like publication stamps."""
    texts: list[str] = []
    publication = False
    if ctx.date_sel is not None:
        joined = " ".join(_block_text(h) for h in ctx.date_sel(el)).strip()
        if joined:
            texts.append(joined)
    dateish: list[Any] = []
    for node in el.iter():
        if not isinstance(node.tag, str) or node is el:
            continue
        toks = _tokens(node)
        if toks & _PUB_TOKENS or {"entry", "date"} <= toks or {"post", "date"} <= toks:
            publication = True
            continue
        if node.get("itemprop", "").lower().startswith("datepublished"):
            publication = True
            continue
        if toks & _DATE_TOKENS and not any(a in dateish for a in node.iterancestors()):
            dateish.append(node)
    if dateish:
        texts.append(" ".join(_block_text(n) for n in dateish))
    for node in el.iter("time"):
        stamp = node.get("datetime")
        if stamp:
            texts.append(stamp)
    texts.append(_block_text(el))
    return texts, publication


def _read_date(el: Any, ctx: _Ctx) -> tuple[ParsedDateRange | None, bool, bool]:
    """(parsed, publication_hint, saw_date_without_year)."""
    texts, publication = _date_texts(el, ctx)
    yearless = False
    for text in texts:
        iso = parse_datetime_any(text.strip()) if len(text) <= 40 else None
        if iso is not None:
            if iso.all_day:
                return ParsedDateRange(start=iso.day, raw=text), publication, yearless
            assert iso.value is not None
            return (
                ParsedDateRange(
                    start=iso.day, start_time=iso.value.timetz().replace(tzinfo=None), raw=text
                ),
                publication,
                yearless,
            )
        parsed = parse_date_range(
            text,
            reference_date=ctx.reference,
            year_policy=ctx.policy,
            context_year=ctx.context_year,
        )
        if parsed is not None:
            return parsed, publication, yearless
        yearless = yearless or mentions_date_without_year(text)
    return None, publication, yearless


def _news_like(el: Any, url: str | None, publication: bool) -> bool:
    if publication:
        return True
    if url:
        segments = {s.lower() for s in urlsplit(url).path.split("/") if s}
        if segments & _NEWS_SEGMENTS:
            return True
    return False


def _venue(el: Any, title: str, text: str) -> str | None:
    match = _VENUE_LABEL.search(text)
    if match:
        value = match.group(1).strip(" .;")
        if value:
            return value[:160]
    best: str | None = None
    for node in el.iter():
        if not isinstance(node.tag, str) or node is el:
            continue
        if _tokens(node) & _VENUE_TOKENS:
            value = _one_line(node)
            value = re.sub(
                r"^(?:lugar|ubicaci[oó]n|venue|location)\s*[:：]\s*", "", value, flags=re.I
            )
            if value and len(value) <= 120 and title not in value:
                best = value.strip(" .;")
    return best or None


def _leaf_text(el: Any, tokens: frozenset[str], limit: int) -> str | None:
    """Text of the innermost element whose class names carry one of ``tokens``."""
    matches = [
        n for n in el.iter() if isinstance(n.tag, str) and n is not el and _tokens(n) & tokens
    ]
    for node in matches:
        if any(other is not node and node in other.iterancestors() for other in matches):
            continue  # an inner match exists: prefer it
        text = _one_line(node)
        if text and len(text) <= limit:
            return text
    return None


def _price(el: Any, text: str) -> tuple[float | None, float | None, str | None, bool | None]:
    scoped = _leaf_text(el, _PRICE_TOKENS, 80)
    if scoped:
        found = parse_price(scoped)
        if found != (None, None, None, None):
            return found
    snippets = _PRICE_SNIPPET.findall(text)
    if snippets:
        return parse_price(" ".join(snippets[:3]))
    for node in el.iter():
        if isinstance(node.tag, str) and len(node) == 0:
            own = " ".join((node.text or "").split())
            if own and len(own) <= 40 and _FREE_WORDS.match(own):
                return parse_price(own)
    return (None, None, None, None)


def _image(el: Any, base: str | None) -> str | None:
    for img in el.iter("img"):
        for attr in ("src", "data-src", "data-lazy-src", "data-original"):
            raw = (img.get(attr) or "").strip()
            if raw and not raw.startswith("data:"):
                url = resolve_url(base, raw)
                if url:
                    return url
    return None


def _combine(day: date, clock: time | None) -> datetime | None:
    return datetime.combine(day, clock, TZ) if clock is not None else None


def _read_member(el: Any, ctx: _Ctx) -> _Read:
    text = _block_text(el)[:MAX_MEMBER_CHARS]
    miss = (ctx.title_sel is not None and not ctx.title_sel(el)) or (
        ctx.date_sel is not None and not ctx.date_sel(el)
    )
    if len(text) < 4:
        return _Read("untitled", hint_miss=miss)
    title, title_el = _find_title(el, ctx)
    if title is None:
        return _Read("untitled", hint_miss=miss)
    parsed, publication, yearless = _read_date(el, ctx)
    if parsed is None:
        return _Read("yearless" if yearless else "undated", hint_miss=miss)
    parsed_title = parse_date_range(title, reference_date=ctx.reference, year_policy="infer")
    if parsed_title is not None and len(parsed_title.raw) >= len(title) - 2:
        return _Read("untitled", hint_miss=miss)  # the "title" is just a date
    url = _detail_url(el, title_el, ctx)
    ranges = len(
        find_date_ranges(
            text,
            reference_date=ctx.reference,
            year_policy=ctx.policy,
            context_year=ctx.context_year,
        )
    )
    if _news_like(el, url, publication):
        return _Read("news", ranges=ranges, hint_miss=miss)
    start_time, end_time = parsed.start_time, parsed.end_time
    if start_time is None:
        start_time, end_time = parse_time_range(text)
    start_dt = _combine(parsed.start, start_time)
    last_day = parsed.end or parsed.start
    end_dt = _combine(last_day, end_time) if end_time is not None and start_dt is not None else None
    if end_dt is not None and start_dt is not None and end_dt < start_dt:
        end_dt = None
    price_min, price_max, currency, is_free = _price(el, text)
    cand = CandidateEvent(
        title=title,
        start_date=None if start_dt else parsed.start,
        start_datetime=start_dt,
        end_date=parsed.end if parsed.end and parsed.end != parsed.start else None,
        end_datetime=end_dt,
        description=_leaf_text(el, _DESCRIPTION_TOKENS, 600),
        category=_leaf_text(el, _CATEGORY_TOKENS, 60),
        venue_name=_venue(el, title, text),
        price_min=price_min,
        price_max=price_max,
        currency=currency,
        is_free=is_free,
        official_event_url=url,
        image_url=_image(el, ctx.base),
        strategy="blocks",
        confidence="high" if url else "medium",
        year_inferred=parsed.year_inferred,
        raw=fragment(text[:RAW_CHARS]),
    )
    if ctx.hints and ctx.hints.block_selector:
        cand.extra["hinted"] = True
    return _Read("ok", cand, ranges, miss)


# --------------------------------------------------------------------------- grouping


def _find_groups(root: Any) -> list[list[Any]]:
    """Repeated elements: same tag and classes anywhere, or same tag under one parent."""
    by_key: dict[Any, list[Any]] = {}
    body = root.find("body") if root.tag != "body" else root
    if body is None:
        body = root
    for el in body.iter():
        if not isinstance(el.tag, str) or el.tag in _SKIP_MEMBER_TAGS or el is body:
            continue
        tag, classes = _signature(el)
        if classes:
            by_key.setdefault((tag, classes), []).append(el)
        by_key.setdefault((tag, id(el.getparent())), []).append(el)
    seen: set[frozenset[int]] = set()
    groups: list[list[Any]] = []
    for members in by_key.values():
        if len(members) < MIN_GROUP:
            continue
        marker = frozenset(id(m) for m in members)
        if marker not in seen:
            seen.add(marker)
            groups.append(members)
    groups.sort(key=len, reverse=True)
    return groups[:MAX_GROUPS]


def _depth(el: Any) -> int:
    return sum(1 for _ in el.iterancestors())


@dataclass
class _Evaluated:
    members: list[Any]
    reads: list[_Read]

    @property
    def ok(self) -> list[_Read]:
        return [r for r in self.reads if r.status == "ok"]

    def count(self, status: str) -> int:
        return sum(1 for r in self.reads if r.status == status)


def _evaluate(group: list[Any], ctx: _Ctx) -> _Evaluated:
    members = group[:MAX_MEMBERS]
    return _Evaluated(members, [_read_member(m, ctx) for m in members])


def _accepted(ev: _Evaluated) -> bool:
    ok = ev.ok
    if len(ok) < 2:
        return False
    aggregates = sum(1 for r in ok if r.ranges >= 5)
    return aggregates <= 0.3 * len(ok)


def _generic(root: Any, ctx: _Ctx, result: BlocksResult) -> list[CandidateEvent]:
    evaluated = [_evaluate(g, ctx) for g in _find_groups(root)]
    good = sorted(
        (ev for ev in evaluated if _accepted(ev)),
        key=lambda ev: (-len(ev.ok), _depth(ev.members[0])),
    )
    chosen: list[_Evaluated] = []
    related: set[int] = set()
    for ev in good:
        if any(id(m) in related for m in ev.members):
            continue
        chosen.append(ev)
        for member in ev.members:
            related.add(id(member))
            related.update(id(d) for d in member.iterdescendants())
            related.update(id(a) for a in member.iterancestors())
    result.groups = len(chosen)
    result.recognized = bool(chosen)
    result.blocks_seen = sum(len(ev.members) for ev in chosen)
    result.undated = sum(ev.count("undated") + ev.count("yearless") for ev in chosen)
    if evaluated:  # nested wrappers repeat the same blocks: take the largest group, not the sum
        result.year_missing = max(ev.count("yearless") for ev in evaluated)
        result.news_like = max(ev.count("news") for ev in evaluated)
    found: list[tuple[int, CandidateEvent]] = []
    order = {id(el): i for i, el in enumerate(root.iter())}
    for ev in chosen:
        for member, read in zip(ev.members, ev.reads, strict=True):
            if read.status == "ok" and read.candidate is not None:
                found.append((order.get(id(member), 0), read.candidate))
    found.sort(key=lambda item: item[0])
    return [cand for _, cand in found]


def _hinted(root: Any, hints: BlockHints, ctx: _Ctx, result: BlocksResult) -> list[CandidateEvent]:
    assert hints.block_selector
    try:
        selector: Any = CSSSelector(hints.block_selector)
        blocks: list[Any] = list(selector(root))
    except Exception as exc:  # noqa: BLE001 - invalid selector text
        result.selector_drift = True
        result.warnings.append(f"selector_drift: invalid block_selector ({type(exc).__name__})")
        return []
    if not blocks:
        result.selector_drift = True
        result.warnings.append(
            "selector_drift: block_selector matched nothing; using generic detection"
        )
        return []
    generic_ctx = _Ctx(ctx.base, ctx.pattern, ctx.reference, ctx.policy, ctx.context_year)
    found: list[CandidateEvent] = []
    missed = 0
    for block in blocks[:MAX_MEMBERS]:
        read = _read_member(block, ctx)
        if read.hint_miss:
            missed += 1
        if read.status != "ok":
            read = _read_member(block, generic_ctx)
        if read.status == "ok" and read.candidate is not None:
            found.append(read.candidate)
        else:
            result.undated += 1
    result.blocks_seen = len(blocks)
    if missed and missed >= len(blocks) / 2:
        result.selector_drift = True
        result.warnings.append(
            "selector_drift: date/title selectors no longer match; used generic fallback"
        )
    if not found:
        result.selector_drift = True
        result.warnings.append(
            "selector_drift: hinted blocks produced no events; using generic detection"
        )
        return []
    result.recognized = True
    result.groups = 1
    return found


def extract_blocks(
    html: str | bytes,
    page_url: str | None = None,
    *,
    detail_url_pattern: str | None = None,
    hints: BlockHints | None = None,
    reference_date: date | None = None,
    year_policy: YearPolicy = "strict",
    context_year: int | None = None,
) -> BlocksResult:
    """Event candidates from repeated blocks; see the module docstring."""
    result = BlocksResult()
    root = parse_html(html)
    if root is None:
        return result
    try:
        pattern = re.compile(detail_url_pattern) if detail_url_pattern else None
    except re.error:
        pattern = None
        result.warnings.append("invalid detail_url_pattern ignored")
    _prune(root)
    ctx = _Ctx(page_url, pattern, reference_date, year_policy, context_year, hints)
    if hints is not None:
        for name in ("title_selector", "date_selector"):
            selector = getattr(hints, name)
            if not selector:
                continue
            try:
                setattr(
                    ctx,
                    "title_sel" if name == "title_selector" else "date_sel",
                    CSSSelector(selector),
                )
            except Exception as exc:  # noqa: BLE001 - invalid selector text
                result.selector_drift = True
                result.warnings.append(f"selector_drift: invalid {name} ({type(exc).__name__})")
    candidates: list[CandidateEvent] = []
    if hints is not None and hints.block_selector:
        candidates = _hinted(root, hints, ctx, result)
    if not candidates:
        generic_ctx = _Ctx(page_url, pattern, reference_date, year_policy, context_year)
        candidates = _generic(root, generic_ctx, result)
    result.candidates = dedupe(candidates)[:MAX_CANDIDATES]
    if result.year_missing and not result.candidates:
        result.warnings.append(
            f"{result.year_missing} dated blocks have no year; set year_policy: infer if intended"
        )
    elif result.year_missing:
        result.warnings.append(f"{result.year_missing} blocks skipped: date without year (strict)")
    if result.news_like:
        result.warnings.append(f"{result.news_like} blocks look like news items, not events")
    return result
