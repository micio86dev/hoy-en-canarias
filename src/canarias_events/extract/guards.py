"""Cheap checks on a fetched page: challenge walls, JS-only pages and content types."""

from __future__ import annotations

import re
from collections.abc import Mapping

from canarias_events.extract.util import parse_html

_CHALLENGE_TITLES = re.compile(
    r"<title[^>]*>\s*(?:just a moment|attention required|access denied|please wait|"
    r"checking your browser|verifying|security check|ddos protection|one more step|"
    r"are you a robot|robot check|pardon our interruption|403 forbidden)",
    re.IGNORECASE,
)
_CHALLENGE_MARKERS = re.compile(
    r"cf-browser-verification|challenge-platform|cf-chl-|_Incapsula_Resource|g-recaptcha|"
    r"h-captcha|hcaptcha|px-captcha|captcha-delivery|Checking your browser|"
    r"enable javascript and cookies to continue",
    re.IGNORECASE,
)
SMALL_PAGE = 30_000

_JS_ROOTS = (
    '//*[@id="__next"] | //*[@id="__nuxt"] | //*[@id="app"] | //*[@id="root"] | //*[@ng-app] '
    "| //*[@data-reactroot] | //*[@data-server-rendered]"
)
_JS_HINTS = re.compile(
    r"admin-ajax|ajax-loading|adt-events-ajax|ng-eventsearch|data-ng-|skeleton|"
    r"enable javascript|habilita(?:r)? javascript|requires javascript",
    re.IGNORECASE,
)
_HTML_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/xml", "text/xml")


class ContentTypeRejected(Exception):
    """The response is not an HTML document (PDF, image, archive, binary)."""


def is_challenge_page(html: str) -> bool:
    """True for CAPTCHA / anti-bot challenge walls. They are reported as blocked, never solved."""
    head = html[:60_000]
    if _CHALLENGE_TITLES.search(head):
        return True
    return len(html) < SMALL_PAGE and _CHALLENGE_MARKERS.search(head) is not None


def _visible_text_length(root: object) -> int:
    for el in root.xpath("//script | //style | //noscript | //template"):  # type: ignore[attr-defined]
        el.drop_tree()
    return len(" ".join(root.text_content().split()))  # type: ignore[attr-defined]


def looks_js_driven(html: str) -> bool:
    """Heuristic: little server-rendered text, or an AJAX placeholder instead of the listing."""
    hinted = _JS_HINTS.search(html[:400_000]) is not None
    root = parse_html(html)
    if root is None:
        return False
    has_root = bool(root.xpath(_JS_ROOTS))
    length = _visible_text_length(root)
    if length < 1200:
        return True
    if length < 4000 and (has_root or hinted):
        return True
    return hinted and length < 6000 and "admin-ajax" in html[:400_000]


def check_html_content_type(headers: Mapping[str, str]) -> None:
    """Reject responses that cannot be an HTML page. A missing header is accepted."""
    raw = headers.get("content-type") or headers.get("Content-Type") or ""
    kind = raw.split(";", 1)[0].strip().lower()
    if kind and kind not in _HTML_TYPES:
        raise ContentTypeRejected(f"unexpected content type: {kind}")
