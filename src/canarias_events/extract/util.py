"""Small helpers shared by the extraction strategies."""

from __future__ import annotations

import math
import re
from typing import Any

from lxml import etree
from lxml import html as lxml_html

from canarias_events.normalize import html_to_text

MAX_HTML_CHARS = 8_000_000
_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)


def parse_html(content: str | bytes) -> Any | None:
    """Parse HTML defensively; returns the root element or None when nothing usable is there."""
    if isinstance(content, str):
        content = _XML_DECLARATION.sub("", content[:MAX_HTML_CHARS]).replace("\x00", "")
        if not content.strip():
            return None
    elif not content.strip():
        return None
    try:
        return lxml_html.fromstring(content[:MAX_HTML_CHARS])
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        return None


def text_of(value: object) -> str | None:
    """Plain text for a scalar (markup stripped, whitespace collapsed); None when empty."""
    if isinstance(value, dict) and "@value" in value:
        value = value["@value"]
    if isinstance(value, bool) or value is None or not isinstance(value, str | int | float):
        return None
    text = html_to_text(str(value))
    text = " ".join(text.split())
    return text or None


def lines_of(value: object) -> list[str]:
    """Non-empty text lines of a scalar, with markup stripped (``<br>`` becomes a line break)."""
    if not isinstance(value, str):
        return []
    return [ln.strip(" ,") for ln in html_to_text(value).splitlines() if ln.strip(" ,")]


def as_number(value: object) -> float | None:
    """Finite float from numbers or numeric strings (``12,50`` allowed); anything else is None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().replace(",", "."))
    except ValueError:
        return None
    return number if math.isfinite(number) else None
