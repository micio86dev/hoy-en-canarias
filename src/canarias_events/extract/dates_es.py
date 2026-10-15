"""Deterministic Spanish (plus English) date and date-range parser for listing snippets.

Regular expressions over a month table come first; ``dateparser`` is only a second attempt for
single dates that the patterns cannot read. A missing year is never guessed unless the caller
passes ``context_year`` or a ``reference_date`` together with ``year_policy="infer"``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, time
from typing import Literal

YearPolicy = Literal["strict", "infer"]

_MONTH_NAMES: dict[str, int] = {
    # Spanish
    "enero": 1, "ene": 1, "febrero": 2, "feb": 2, "marzo": 3, "mar": 3, "abril": 4, "abr": 4,
    "mayo": 5, "may": 5, "junio": 6, "jun": 6, "julio": 7, "jul": 7, "agosto": 8, "ago": 8,
    "septiembre": 9, "setiembre": 9, "sept": 9, "sep": 9, "set": 9, "octubre": 10, "oct": 10,
    "noviembre": 11, "nov": 11, "diciembre": 12, "dic": 12,
    # English (those that differ from Spanish)
    "january": 1, "jan": 1, "february": 2, "march": 3, "april": 4, "apr": 4, "june": 6,
    "july": 7, "august": 8, "aug": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "dec": 12,
}  # fmt: skip
_ENGLISH_ONLY_MONTHS = frozenset(
    {"january", "jan", "february", "march", "april", "apr", "june", "july", "august", "aug",
     "september", "october", "november", "december", "dec", "may", "sep", "sept", "oct", "nov",
     "jul", "jun", "feb"}
)  # fmt: skip
_MON = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))
_YEAR = r"(?:19|20|21)\d{2}"
_DAY_GUARD = r"(?<![\d/:.\w])"
_SEP = r"(?:\s*(?:-|–|—|>|›|»)\s*|\s+(?:al|a|hasta(?:\s+el)?|to|until)\s+)"


def _dm(i: int) -> str:
    return (
        rf"{_DAY_GUARD}(?P<d{i}>\d{{1,2}})(?:st|nd|rd|th|º|°|o(?![a-z]))?(?![\d:/]|\.\d)"
        rf"\s*(?:,|-|/)?\s*(?:(?:de|del)\s+)?"
        rf"(?P<m{i}>{_MON})(?![a-z])\.?"
        rf"(?!(?:-|/|')\d{{2}}(?!\d)(?!\s*(?:de\s+)?(?:{_MON})(?![a-z])))"
        rf"(?:\s*(?:,|-|/|de|del)?\s*(?P<y{i}>{_YEAR})(?!\d))?"
    )


_RE_DM = re.compile(_dm(0))
_RE_NUMERIC = re.compile(
    rf"{_DAY_GUARD}(?P<d>\d{{1,2}})(?P<s>[/.-])(?P<m>\d{{1,2}})(?P=s)"
    rf"(?P<y>\d{{4}}|\d{{2}})(?![\d:/]|[./-]\d)"
)
_RE_ISO = re.compile(r"(?<![\d/:.])(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?!\d)")
_RE_MONTH_FIRST = re.compile(
    rf"(?<![a-z\d])(?P<m>{_MON})(?![a-z])\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?![\d:/]|\.\d)"
    rf"(?:\s*,?\s*(?P<y>{_YEAR})(?!\d))?"
)
_RE_WEEKDAY_GLUE = re.compile(r"(?<![a-z0-9])[lmxjvsd](?=\d{1,2}\s+(?:de\s+)?[a-z]{3})")
_RE_DAY_ONLY_BEFORE = re.compile(rf"{_DAY_GUARD}(?P<d>\d{{1,2}})(?![\d:/]|\.\d){_SEP}$")
_RE_SEP_ONLY_BEFORE = re.compile(rf"{_SEP}$")
_RE_WS_ONLY = re.compile(r"\s*$")

# Closest year to the reference date, but never more than ~10 months in the past.
_MAX_DAYS_IN_PAST = 304
MAX_TEXT_CHARS = 50_000


@dataclass(frozen=True)
class ParsedDateRange:
    start: date
    end: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    year_inferred: bool = False
    raw: str = ""


@dataclass
class _Item:
    day: int
    month: int
    year: int | None
    start: int
    end: int
    kind: str


def _fold(text: str) -> str:
    """Lowercase, strip accents and unify spaces, keeping the string length (offsets stay valid)."""
    out: list[str] = []
    for ch in text:
        if ch in "\u00a0\u2009\u202f\u200b":
            out.append(" ")
        elif ord(ch) < 128:
            out.append(ch.lower())
        else:
            out.append(unicodedata.normalize("NFD", ch)[0].lower())
    return "".join(out)


def _valid(year: int, month: int, day: int) -> date | None:
    if not 1990 <= year <= 2100:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _scan_items(folded: str) -> list[_Item]:
    items: list[_Item] = []
    for m in _RE_DM.finditer(folded):
        year = int(m.group("y0")) if m.group("y0") else None
        items.append(
            _Item(int(m.group("d0")), _MONTH_NAMES[m.group("m0")], year, m.start(), m.end(), "dm")
        )
    for m in _RE_NUMERIC.finditer(folded):
        raw_year = m.group("y")
        if len(raw_year) == 2 and m.group("s") != "/":
            continue
        year = int(raw_year) + (2000 if len(raw_year) == 2 else 0)
        items.append(_Item(int(m.group("d")), int(m.group("m")), year, m.start(), m.end(), "num"))
    for m in _RE_ISO.finditer(folded):
        items.append(
            _Item(
                int(m.group("d")), int(m.group("m")), int(m.group("y")), m.start(), m.end(), "iso"
            )
        )
    for m in _RE_MONTH_FIRST.finditer(folded):
        if m.group("m") not in _ENGLISH_ONLY_MONTHS:
            continue
        year = int(m.group("y")) if m.group("y") else None
        items.append(
            _Item(int(m.group("d")), _MONTH_NAMES[m.group("m")], year, m.start(), m.end(), "mf")
        )
    items.sort(key=lambda it: (it.start, it.start - it.end))
    chosen: list[_Item] = []
    for item in items:
        if chosen and item.start < chosen[-1].end:
            continue
        chosen.append(item)
    return chosen


# --------------------------------------------------------------------------- times

_TIME_TOKEN = (
    r"(?<![\d:.,/\w-])(?P<{p}h>\d{{1,2}})(?:(?P<{p}s>[:.])(?P<{p}mi>\d{{2}})(?::\d{{2}})?)?"
    r"(?:\s*(?P<{p}ap>[ap]\.?m\.?)(?![a-z]))?(?:\s*(?P<{p}hs>(?:h|hs|hrs|horas?)(?![a-z])\.?))?"
    r"(?![\d]|:\d|[./]\d)"
)
_RE_TIME_RANGE = re.compile(
    _TIME_TOKEN.format(p="a")
    + r"\s*(?:-|–|—|a|hasta|to)\s*"
    + _TIME_TOKEN.format(p="b").replace(r"(?<![\d:.,/\w-])", r"(?<![\d:.,/])")
)
_RE_TIME = re.compile(_TIME_TOKEN.format(p="a"))


def _make_time(hour: str, minute: str | None, ap: str | None) -> time | None:
    h = int(hour)
    mi = int(minute) if minute else 0
    if ap:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ap.startswith("p") else 0)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return time(h, mi)


def _token_ok(sep: str | None, has_h: bool, has_ap: bool) -> bool:
    if sep == ":":
        return True
    return has_h or has_ap  # "18.00 horas", "20h", "8 pm"; a bare "1.2" is not a time


def _times_in(folded: str) -> tuple[time | None, time | None]:
    for m in _RE_TIME_RANGE.finditer(folded):
        first = _make_time(m.group("ah"), m.group("ami"), m.group("aap"))
        second = _make_time(m.group("bh"), m.group("bmi"), m.group("bap"))
        if first is None or second is None:
            continue
        sep_a, sep_b = m.group("as"), m.group("bs")
        if sep_a is None or sep_b is None:
            continue  # "20 a 21" is not a time range
        has_h = bool(m.group("ahs") or m.group("bhs"))
        if (sep_a == ":" or sep_b == ":") or has_h:
            return first, second
    for m in _RE_TIME.finditer(folded):
        value = _make_time(m.group("ah"), m.group("ami"), m.group("aap"))
        if value is None:
            continue
        if _token_ok(m.group("as"), bool(m.group("ahs")), bool(m.group("aap"))):
            return value, None
    return None, None


def parse_time_range(text: str) -> tuple[time | None, time | None]:
    """First time (or time range) in ``text``: ``20:30h``, ``de 10:00 a 14:00``, ``8:30 pm``."""
    if not text:
        return None, None
    return _times_in(_fold(text[:MAX_TEXT_CHARS]))


# --------------------------------------------------------------------------- years


def _infer_year(month: int, day: int, reference: date) -> int | None:
    best: tuple[int, int] | None = None
    for year in (reference.year - 1, reference.year, reference.year + 1):
        candidate = _valid(year, month, day)
        if candidate is None:
            continue
        delta = (candidate - reference).days
        if delta < -_MAX_DAYS_IN_PAST:
            continue
        if best is None or abs(delta) < best[0]:
            best = (abs(delta), year)
    return best[1] if best else None


@dataclass
class _Resolver:
    reference: date | None
    policy: YearPolicy
    context_year: int | None

    def single(self, item: _Item) -> tuple[date, bool] | None:
        if item.year is not None:
            found = _valid(item.year, item.month, item.day)
            return (found, False) if found else None
        year = self._fallback_year(item)
        if year is None:
            return None
        found = _valid(year, item.month, item.day)
        return (found, True) if found else None

    def _fallback_year(self, item: _Item) -> int | None:
        if self.context_year is not None:
            return self.context_year
        if self.policy == "infer" and self.reference is not None:
            return _infer_year(item.month, item.day, self.reference)
        return None

    def pair(self, first: _Item, second: _Item) -> tuple[date, date, bool] | None:
        inferred = False
        y1, y2 = first.year, second.year
        if y1 is None and y2 is None:
            start = self.single(first)
            if start is None:
                return None
            inferred = True
            y1 = start[0].year
            y2 = y1
            end = _valid(y2, second.month, second.day)
            if end is not None and end < start[0]:
                end = _valid(y2 + 1, second.month, second.day)
            if end is None:
                return None
            return start[0], end, inferred
        if y1 is None and y2 is not None:
            y1 = y2
            if (second.month, second.day) < (first.month, first.day):
                y1 = y2 - 1
        elif y2 is None and y1 is not None:
            y2 = y1
            if (second.month, second.day) < (first.month, first.day):
                y2 = y1 + 1
        assert y1 is not None and y2 is not None
        start_d = _valid(y1, first.month, first.day)
        end_d = _valid(y2, second.month, second.day)
        if start_d is None or end_d is None:
            return None
        return start_d, end_d, inferred


# --------------------------------------------------------------------------- ranges


@dataclass
class _Span:
    first: _Item
    second: _Item | None
    start: int
    end: int


def _group(folded: str, items: list[_Item]) -> list[_Span]:
    spans: list[_Span] = []
    taken = 0
    for index, item in enumerate(items):
        if index < taken:
            continue
        # "19-23 oct", "Del 15 al 19 de octubre": a bare day, a separator, then a full date.
        window_start = max(0, item.start - 40)
        before = _RE_DAY_ONLY_BEFORE.search(folded[window_start : item.start])
        if before and (not spans or window_start + before.start() >= spans[-1].end):
            day = int(before.group("d"))
            month, year = item.month, item.year
            if day > item.day:
                month -= 1
                if month == 0:
                    month, year = 12, (year - 1) if year is not None else None
            lead_start = window_start + before.start()
            lead = _Item(day, month, year, lead_start, window_start + before.end("d"), "day")
            spans.append(_Span(lead, item, lead_start, item.end))
            continue
        nxt = items[index + 1] if index + 1 < len(items) else None
        if nxt is not None:
            between = folded[item.end : nxt.start]
            if _RE_SEP_ONLY_BEFORE.fullmatch(between) or (
                _RE_WS_ONLY.fullmatch(between) and item.year is None and nxt.year is None
            ):
                spans.append(_Span(item, nxt, item.start, nxt.end))
                taken = index + 2
                continue
        spans.append(_Span(item, None, item.start, item.end))
    return spans


def _times_for(folded: str, spans: list[_Span], index: int) -> tuple[time | None, time | None]:
    span = spans[index]
    after_end = spans[index + 1].start if index + 1 < len(spans) else len(folded)
    found = _times_in(folded[span.end : after_end])
    if found != (None, None):
        return found
    before_start = spans[index - 1].end if index > 0 else 0
    return _times_in(folded[before_start : span.start])


def find_date_ranges(
    text: str,
    *,
    reference_date: date | None = None,
    year_policy: YearPolicy = "strict",
    context_year: int | None = None,
) -> list[ParsedDateRange]:
    """Every resolvable date or date range in ``text``, in reading order."""
    if not text or not text.strip():
        return []
    text = text[:MAX_TEXT_CHARS]
    folded = _fold(text)
    folded = _RE_WEEKDAY_GLUE.sub(" ", folded)
    resolver = _Resolver(reference_date, year_policy, context_year)
    spans = _group(folded, _scan_items(folded))
    results: list[ParsedDateRange] = []
    for index, span in enumerate(spans):
        if span.second is None:
            single = resolver.single(span.first)
            if single is None:
                continue
            start, end, inferred = single[0], None, single[1]
        else:
            pair = resolver.pair(span.first, span.second)
            if pair is None:
                continue
            start, end, inferred = pair
            if end == start:
                end = None
            elif end < start:
                continue
        start_time, end_time = _times_for(folded, spans, index)
        results.append(
            ParsedDateRange(
                start=start,
                end=end,
                start_time=start_time,
                end_time=end_time,
                year_inferred=inferred,
                raw=text[span.start : span.end].strip(),
            )
        )
    return results


def mentions_date_without_year(text: str) -> bool:
    """True when ``text`` has a day-and-month mention that carries no year (diagnostics only)."""
    folded = _RE_WEEKDAY_GLUE.sub(" ", _fold(text))
    return any(item.year is None for item in _scan_items(folded))


_RE_FALLBACK_SHAPE = re.compile(rf"\d.*(?:{_MON})(?![a-z]).*\d|\d.*\d.*(?:{_MON})(?![a-z])")


def _dateparser_single(text: str) -> ParsedDateRange | None:
    folded = _fold(text)
    if len(text) > 80 or not _RE_FALLBACK_SHAPE.search(folded):
        return None
    try:
        import dateparser
    except ImportError:  # pragma: no cover - dateparser is a core dependency
        return None
    parsed = dateparser.parse(
        text,
        languages=["es", "en"],
        settings={
            "DATE_ORDER": "DMY",
            "TIMEZONE": "Atlantic/Canary",
            "PREFER_DATES_FROM": "future",
            "STRICT_PARSING": True,
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )
    if parsed is None or not 1990 <= parsed.year <= 2100:
        return None
    # The day written in the text must be the parsed day: never accept relative words or noise.
    if not re.search(rf"(?<!\d){parsed.day}(?!\d)|(?<!\d){parsed.day:02d}(?!\d)", folded):
        return None
    start_time, end_time = _times_in(folded)
    return ParsedDateRange(
        start=parsed.date(),
        start_time=start_time,
        end_time=end_time,
        raw=text.strip(),
    )


def parse_date_range(
    text: str,
    *,
    reference_date: date | None = None,
    year_policy: YearPolicy = "strict",
    context_year: int | None = None,
) -> ParsedDateRange | None:
    """First date or date range in ``text``; None when there is none or the year is unknown."""
    found = find_date_ranges(
        text, reference_date=reference_date, year_policy=year_policy, context_year=context_year
    )
    if found:
        return found[0]
    if not text or not text.strip():
        return None
    return _dateparser_single(text)


__all__ = [
    "ParsedDateRange",
    "YearPolicy",
    "find_date_ranges",
    "mentions_date_without_year",
    "parse_date_range",
    "parse_time_range",
]
