"""HTML to compact markdown-ish text (headings, list items, ``[text](url)`` links)."""

from __future__ import annotations

import importlib.util
import re
from typing import Any

from canarias_events.extract.candidate import resolve_url
from canarias_events.extract.util import parse_html

DEFAULT_MAX_CHARS = 200_000
_DROP = ("script", "style", "noscript", "template", "svg", "iframe", "nav", "form", "canvas")
_BLOCK = frozenset(
    {"p", "div", "section", "article", "main", "ul", "ol", "table", "tr", "blockquote", "pre",
     "figure", "figcaption", "dl", "dt", "dd", "address", "details", "summary", "aside"}
)  # fmt: skip
_HEADINGS = {f"h{n}": "#" * n for n in range(1, 7)}


def _prune(root: Any) -> None:
    for el in list(root.iter(*_DROP)):
        el.drop_tree()
    for el in list(root.iter("header", "footer")):
        if not any(a.tag in ("article", "li", "main", "section") for a in el.iterancestors()):
            el.drop_tree()


def _walk(el: Any, base: str | None, out: list[str], depth: int) -> None:
    if depth > 200 or not isinstance(el.tag, str):
        return
    tag = el.tag
    if tag in _HEADINGS:
        out.append(f"\n\n{_HEADINGS[tag]} {' '.join(el.text_content().split())}\n\n")
        return
    if tag == "li":
        out.append("\n- ")
    elif tag == "br":
        out.append("\n")
    elif tag in _BLOCK:
        out.append("\n\n")
    if tag == "a":
        label = " ".join(el.text_content().split())
        url = resolve_url(base, el.get("href"))
        if label and url:
            out.append(f"[{label}]({url})")
        elif label:
            out.append(label)
        if el.tail:
            out.append(el.tail)
        return
    if el.text:
        out.append(el.text)
    for child in el:
        _walk(child, base, out, depth + 1)
        if child.tail and isinstance(child.tag, str) and child.tag != "a":
            out.append(child.tail)
    if tag in _BLOCK:
        out.append("\n\n")


def html_to_markdownish(
    html: str | bytes,
    base_url: str | None = None,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    prefer_trafilatura: bool = False,
) -> str:
    """Clean text for reading or for a local model. Page text is data: it is never interpreted.

    ``prefer_trafilatura`` uses the optional ``trafilatura`` package when installed (it targets
    article bodies, so it is off by default for listings).
    """
    if prefer_trafilatura and importlib.util.find_spec("trafilatura") is not None:
        import trafilatura

        extracted = trafilatura.extract(
            html if isinstance(html, str) else html.decode("utf-8", "replace"),
            output_format="markdown",
            include_links=True,
        )
        if extracted:
            return str(extracted)[:max_chars]
    root = parse_html(html)
    if root is None:
        return ""
    _prune(root)
    out: list[str] = []
    for el in root.iter("body"):
        _walk(el, base_url, out, 0)
        break
    else:
        _walk(root, base_url, out, 0)
    text = "".join(out).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars]
