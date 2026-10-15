"""Pure normalization helpers: HTML to text, prices, titles, dates and URLs."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from canarias_events.date_range import TZ

# --------------------------------------------------------------------------- html_to_text

_BLOCK_TAGS = frozenset(
    {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "section", "article",
     "blockquote", "pre", "header", "footer", "dl"}
)  # fmt: skip
_LINE_TAGS = frozenset({"li", "tr", "dt", "dd"})
_SKIP_TAGS = frozenset({"script", "style", "head", "template"})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "br":
            self.parts.append("\n")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n\n")
        elif tag in _LINE_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str | None) -> str:
    """Strip markup, decode entities, collapse whitespace and keep paragraph breaks."""
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    text = re.sub(r"[^\S\n]+", " ", "".join(parser.parts))
    text = "\n".join(line.strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --------------------------------------------------------------------------- parse_price

PriceInfo = tuple[float | None, float | None, str | None, bool | None]

_CUR = r"(?:€|\$|£|(?:euros?|eur|usd|gbp)\b)"
_NUM = r"(?:\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_SEP = r"(?:\s*[-–—]\s*|\s+(?:a|to|y)\s+)"
_AMOUNT = re.compile(
    rf"(?:(?P<pre>{_CUR})\s*)?(?P<n1>{_NUM})(?:\s*(?P<post1>{_CUR}))?"
    rf"(?:{_SEP}(?:(?P<pre2>{_CUR})\s*)?(?P<n2>{_NUM})(?:\s*(?P<post2>{_CUR}))?)?",
    re.IGNORECASE,
)
_NUMERIC_ONLY = re.compile(rf"{_NUM}(?:\s*[-–—]\s*{_NUM})?")
_FREE = re.compile(
    r"\b(gratis|gratuit[oa]s?|entrada libre|entrada gratuita|acceso libre|libre acceso|"
    r"sin coste|sin costo|free(?: entry| admission)?)\b"
)
_FROM = re.compile(r"\b(desde|a partir de|from)\b")
_UP_TO = re.compile(r"\b(hasta|up to)\b")
_CURRENCY_CODES = {"€": "EUR", "euro": "EUR", "euros": "EUR", "eur": "EUR",
                   "$": "USD", "usd": "USD", "£": "GBP", "gbp": "GBP"}  # fmt: skip


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _to_float(raw: str) -> float:
    seps = [c for c in raw if c in ".,"]
    if not seps:
        return float(raw)
    last = max(raw.rfind("."), raw.rfind(","))
    decimals = raw[last + 1 :]
    kinds = set(seps)
    if len(kinds) == 1 and (len(seps) > 1 or len(decimals) == 3):
        return float(raw.replace(".", "").replace(",", ""))  # thousands separators only
    integer = raw[:last].replace(".", "").replace(",", "")
    return float(f"{integer}.{decimals}")


def parse_price(text: str | None) -> PriceInfo:
    """Return (min, max, currency, is_free). Never guesses: unknown text gives all None."""
    if not text or not text.strip():
        return (None, None, None, None)
    plain = _strip_accents(text).casefold()
    values: list[float] = []
    currency: str | None = None
    for match in _AMOUNT.finditer(text):
        markers = [match.group(g) for g in ("pre", "post1", "pre2", "post2") if match.group(g)]
        if not markers:
            continue
        currency = currency or _CURRENCY_CODES[markers[0].casefold()]
        values.append(_to_float(match.group("n1")))
        if match.group("n2"):
            values.append(_to_float(match.group("n2")))
    if not values and _NUMERIC_ONLY.fullmatch(text.strip()):
        values = [_to_float(n) for n in re.findall(_NUM, text)]
    free_word = _FREE.search(plain) is not None
    if not values:
        return (0.0, 0.0, None, True) if free_word else (None, None, None, None)
    low: float | None = min(values)
    high: float | None = max(values)
    if len(values) == 1:
        if _FROM.search(plain):
            high = None
        elif _UP_TO.search(plain):
            low = None
    if free_word:
        low = 0.0  # some tickets are free; the paid maximum (if any) stays in `high`
    return (low, high, currency, all(v == 0 for v in values))


# --------------------------------------------------------------------------- titles


def normalize_title(value: str) -> str:
    """Casefold, strip accents, drop punctuation/emojis and collapse spaces (for dedup)."""
    text = _strip_accents(value).casefold()
    return re.sub(r"[\W_]+", " ", text).strip()


# --------------------------------------------------------------------------- dates


@dataclass(frozen=True)
class ParsedMoment:
    """A parsed instant (``value``, aware) or a date-only value (``value`` None, all_day)."""

    value: datetime | None
    day: date
    all_day: bool


def _from_datetime(value: datetime, default_tz: tzinfo) -> ParsedMoment:
    aware = (
        value.replace(tzinfo=default_tz) if value.tzinfo is None else value.astimezone(default_tz)
    )
    return ParsedMoment(aware, aware.date(), False)


def parse_datetime_any(value: object, default_tz: tzinfo = TZ) -> ParsedMoment | None:
    """Parse ISO strings, 'YYYY-MM-DD HH:MM:SS', date-only values and YYYYMMDD ints."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return _from_datetime(value, default_tz)
    if isinstance(value, date):
        return ParsedMoment(None, value, True)
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None
    if not text:
        return None
    try:
        if re.fullmatch(r"\d{8}", text):
            return ParsedMoment(None, date(int(text[:4]), int(text[4:6]), int(text[6:])), True)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return ParsedMoment(None, date.fromisoformat(text), True)
        return _from_datetime(datetime.fromisoformat(text), default_tz)
    except ValueError:
        return None


# --------------------------------------------------------------------------- URLs

_TRACKING_PARAMS = frozenset({"fbclid", "gclid"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def canonical_url(url: str) -> str:
    """Canonical form for matching: lowercase scheme/host, no userinfo, no ``www.``, no fragment,
    tracking params, default port or trailing slash."""
    raw = url.strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return raw
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").removeprefix("www.")
    if ":" in host:
        host = f"[{host}]"
    netloc = host
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    )
    return urlunsplit((scheme, netloc, parts.path.rstrip("/"), urlencode(query), ""))
