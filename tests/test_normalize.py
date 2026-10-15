from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from canarias_events.date_range import TZ
from canarias_events.normalize import (
    canonical_url,
    html_to_text,
    normalize_title,
    parse_datetime_any,
    parse_price,
)

NONE_PRICE = (None, None, None, None)

# --------------------------------------------------------------------------- parse_price


@pytest.mark.parametrize(
    "text", [None, "", "   ", "Consultar", "Entradas a la venta el 15 de octubre"]
)
def test_price_unknown_is_all_none(text: str | None) -> None:
    assert parse_price(text) == NONE_PRICE


@pytest.mark.parametrize(
    "text",
    [
        "Gratis",
        "gratuito",
        "Entrada libre",
        "ENTRADA GRATUITA",
        "Free",
        "Free entry",
        "Acceso libre",
    ],
)
def test_price_free(text: str) -> None:
    assert parse_price(text) == (0.0, 0.0, None, True)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10€", (10.0, 10.0, "EUR", False)),
        ("10 €", (10.0, 10.0, "EUR", False)),
        ("€10", (10.0, 10.0, "EUR", False)),
        ("10,50 €", (10.5, 10.5, "EUR", False)),
        ("10.50 EUR", (10.5, 10.5, "EUR", False)),
        ("12 euros", (12.0, 12.0, "EUR", False)),
        ("1.250,00 €", (1250.0, 1250.0, "EUR", False)),
        ("10 - 15 €", (10.0, 15.0, "EUR", False)),
        ("10€ - 15€", (10.0, 15.0, "EUR", False)),
        ("10 a 15 €", (10.0, 15.0, "EUR", False)),
        ("Desde 12€", (12.0, None, "EUR", False)),
        ("desde 12,50 €", (12.5, None, "EUR", False)),
        ("Hasta 20€", (None, 20.0, "EUR", False)),
        ("Adultos 10€, niños 5€", (5.0, 10.0, "EUR", False)),
        ("$25", (25.0, 25.0, "USD", False)),
        ("0 €", (0.0, 0.0, "EUR", True)),
        ("Concierto 20:00h, 10€", (10.0, 10.0, "EUR", False)),
        ("15", (15.0, 15.0, None, False)),
    ],
)
def test_price_amounts(
    text: str, expected: tuple[float | None, float | None, str | None, bool]
) -> None:
    assert parse_price(text) == expected


def test_price_mixed_free_and_paid_is_not_entirely_free() -> None:
    assert parse_price("Gratis para niños, adultos 10€") == (0.0, 10.0, "EUR", False)


# --------------------------------------------------------------------------- html_to_text


def test_html_to_text_paragraphs_and_entities() -> None:
    html = "<p>Hello&nbsp;<b>world</b> &amp; &eacute;</p><p>Second   para</p>"
    assert html_to_text(html) == "Hello world & é\n\nSecond para"


def test_html_to_text_br_and_lists() -> None:
    assert html_to_text("a<br>b<br/>c") == "a\nb\nc"
    assert html_to_text("<ul><li>one</li><li>two</li></ul>") == "one\ntwo"


def test_html_to_text_drops_script_and_style() -> None:
    assert html_to_text("<style>p{}</style><script>alert(1)</script><p>ok</p>") == "ok"


def test_html_to_text_collapses_whitespace_and_blank_runs() -> None:
    assert html_to_text("  <div>a</div>\n\n\n<div>   b \t c </div>  ") == "a\n\nb c"


def test_html_to_text_plain_and_empty() -> None:
    assert html_to_text("just   text") == "just text"
    assert html_to_text("") == ""
    assert html_to_text(None) == ""


def test_html_to_text_broken_markup_does_not_raise() -> None:
    assert html_to_text("<p>unclosed <b>bold") == "unclosed bold"


# --------------------------------------------------------------------------- normalize_title


def test_normalize_title_accents_case_punctuation() -> None:
    assert normalize_title("  ¡Fiesta de San Cristóbal!  ") == "fiesta de san cristobal"


def test_normalize_title_emojis_and_spaces() -> None:
    assert normalize_title("Concierto 🎉🎶  de   Verano — 2026 🌴") == "concierto de verano 2026"


def test_normalize_title_enye_and_equivalence() -> None:
    assert normalize_title("Ñandú") == normalize_title("nandu")
    assert normalize_title("A-B_C") == "a b c"


def test_normalize_title_empty() -> None:
    assert normalize_title("") == ""
    assert normalize_title("🎉") == ""


# --------------------------------------------------------------------------- parse_datetime_any


def test_parse_iso_with_offset_converted_to_default_tz() -> None:
    p = parse_datetime_any("2026-10-19T20:00:00+02:00", TZ)
    assert p is not None
    assert p.all_day is False
    assert p.value == datetime(2026, 10, 19, 18, 0, tzinfo=UTC)
    assert p.value is not None and p.value.utcoffset() is not None
    assert p.day == date(2026, 10, 19)


def test_parse_iso_z_suffix() -> None:
    p = parse_datetime_any("2026-10-19T23:30:00Z", TZ)
    assert p is not None and p.value == datetime(2026, 10, 19, 23, 30, tzinfo=UTC)
    assert p.day == date(2026, 10, 20)  # 00:30 local on the 20th (UTC+1)


def test_parse_naive_uses_default_tz() -> None:
    p = parse_datetime_any("2026-10-19 20:00:00", TZ)
    assert p is not None and p.value == datetime(2026, 10, 19, 20, 0, tzinfo=TZ)
    madrid = parse_datetime_any("2026-10-19 20:00:00", ZoneInfo("Europe/Madrid"))
    assert madrid is not None and madrid.value == datetime(2026, 10, 19, 19, 0, tzinfo=TZ)


def test_parse_naive_iso_t_separator() -> None:
    p = parse_datetime_any("2026-10-19T08:15:00", TZ)
    assert p is not None and p.value == datetime(2026, 10, 19, 8, 15, tzinfo=TZ)


def test_parse_date_only_is_all_day() -> None:
    p = parse_datetime_any("2026-10-19", TZ)
    assert p is not None
    assert p.all_day is True and p.value is None and p.day == date(2026, 10, 19)


@pytest.mark.parametrize("raw", [20261019, "20261019"])
def test_parse_yyyymmdd(raw: int | str) -> None:
    p = parse_datetime_any(raw, TZ)
    assert p is not None and p.all_day is True and p.day == date(2026, 10, 19)


def test_parse_ical_basic_datetime() -> None:
    p = parse_datetime_any("20261019T200000", TZ)
    assert p is not None and p.value == datetime(2026, 10, 19, 20, 0, tzinfo=TZ)


def test_parse_passthrough_objects() -> None:
    p = parse_datetime_any(datetime(2026, 10, 19, 20, 0), TZ)
    assert p is not None and p.value == datetime(2026, 10, 19, 20, 0, tzinfo=TZ)
    d = parse_datetime_any(date(2026, 10, 19), TZ)
    assert d is not None and d.all_day is True


@pytest.mark.parametrize(
    "raw", [None, "", "  ", "not a date", "2026-13-45", 20261345, "mañana", True]
)
def test_parse_garbage_returns_none(raw: object) -> None:
    assert parse_datetime_any(raw, TZ) is None


# --------------------------------------------------------------------------- canonical_url


def test_canonical_url_basics() -> None:
    assert (
        canonical_url("HTTPS://Example.COM/Path/?utm_source=x&b=2&a=1&fbclid=zzz#frag")
        == "https://example.com/Path?a=1&b=2"
    )


def test_canonical_url_trailing_slash_and_root() -> None:
    assert canonical_url("https://a.test/") == "https://a.test"
    assert canonical_url("https://a.test/x/y/") == "https://a.test/x/y"


def test_canonical_url_default_port_removed() -> None:
    assert canonical_url("http://a.test:80/x") == "http://a.test/x"
    assert canonical_url("https://a.test:443/x") == "https://a.test/x"
    assert canonical_url("https://a.test:8443/x") == "https://a.test:8443/x"


def test_canonical_url_equivalence() -> None:
    assert canonical_url("https://a.test/e/1/?utm_campaign=z") == canonical_url(
        "https://A.test/e/1#top"
    )


def test_canonical_url_empty() -> None:
    assert canonical_url("") == ""
    assert canonical_url("   ") == ""
