"""Source candidates: identities, link extraction and what discovery reports turn into.

A candidate is only a note that a place might be worth a look. Nothing here enables, runs or
registers a source; ``validate-source`` scores a candidate and a person decides.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

from canarias_events.extract.util import parse_html
from canarias_events.normalize import canonical_url, normalize_title
from canarias_events.social import classify_social_url

_SECOND_LEVEL = frozenset({"com", "org", "net", "edu", "gob", "co"})
_AGENDA_WORDS = (
    "agenda", "evento", "programacion", "actividad", "calendario", "cultura", "fiesta",
    "cartelera", "espectaculo", "what s on", "events",
)  # fmt: skip
_ID_CHARS = 40


@dataclass(frozen=True)
class FoundCandidate:
    url: str
    kind: str  # web | feed | api | dataset | social
    discovered_via: str


def candidate_id(url: str) -> str:
    """Readable and unique: host and path as a slug plus a short hash of the whole URL."""
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    slug = re.sub(r"[^a-z0-9]+", "-", f"{host}{parts.path}".lower()).strip("-")
    return f"{slug[:_ID_CHARS].strip('-')}-{hashlib.sha1(url.encode()).hexdigest()[:6]}"


def registrable_domain(host: str) -> str:
    """Last two labels (three after com/org/gob...): a heuristic, with no public-suffix list."""
    host = host.lower().strip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return host
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _agenda_like(href: str, text: str) -> bool:
    folded = normalize_title(f"{urlsplit(href).path} {text}")
    return any(word in folded for word in _AGENDA_WORDS)


def extract_candidate_links(
    html: str, base_url: str, *, official_domains: set[str], known: set[str], limit: int
) -> list[str]:
    """Agenda-like links of a page: same registrable domain or a listed official domain only."""
    root = parse_html(html)
    if root is None:
        return []
    allowed = {registrable_domain(urlsplit(base_url).hostname or ""), *official_domains}
    seen = {canonical_url(u) for u in known}
    found: list[str] = []
    for anchor in root.xpath("//a[@href]"):
        href = urljoin(base_url, (anchor.get("href") or "").strip())
        parts = urlsplit(href)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            continue
        if registrable_domain(parts.hostname) not in allowed or classify_social_url(href):
            continue
        if not _agenda_like(href, anchor.text_content() or ""):
            continue
        key = canonical_url(href)
        if key in seen:
            continue
        seen.add(key)
        found.append(href)
        if len(found) >= limit:
            break
    return found


def candidates_from_report(report: dict[str, Any]) -> list[FoundCandidate]:
    """Positive (manual check) results of a discovery report, as candidates."""
    found: dict[str, FoundCandidate] = {}

    def add(url: str | None, kind: str, via: str) -> None:
        if url and url not in found:
            found[url] = FoundCandidate(url, kind, via)

    for result in report.get("results", []):
        if not result.get("needs_manual_check"):
            continue
        via, endpoint = str(result.get("kind")), str(result.get("endpoint", ""))
        if via == "ckan_search":
            root = endpoint.split("/api/3/action/")[0]
            for dataset in result.get("datasets", []):
                if dataset.get("name"):
                    add(f"{root}/dataset/{dataset['name']}", "dataset", via)
        elif via == "datos_gob_es":
            for dataset in result.get("datasets", []):
                add(dataset.get("url"), "dataset", via)
        else:
            add(endpoint, "feed" if via == "ical" else "api", via)
    return list(found.values())
