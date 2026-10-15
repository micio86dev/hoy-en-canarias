"""Non-crawling source reconnaissance: open-data catalog searches and a few endpoint probes.

Nothing found here is ever enabled automatically; positives are flagged for a manual check.
All requests go through :class:`SafeHttpClient`, which rate limits per host.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlsplit

from canarias_events.candidates import FoundCandidate, extract_candidate_links, registrable_domain
from canarias_events.extract.robots import RobotsPolicy
from canarias_events.http import FetchError, SafeHttpClient, mask_secrets
from canarias_events.sources.base import SourceConfig

DEFAULT_TERMS = ("agenda", "eventos", "actividades", "fiestas")
CKAN_PORTALS = (
    "https://datos.canarias.es/catalogos/general/api/3/action/",
    "https://datos.tenerife.es/ckan/api/3/action/",
    "https://www.santacruzdetenerife.es/opendata/api/3/action/",
)
DATOS_GOB_ES_URL = "https://datos.gob.es/apidata/catalog/dataset/title/{term}.json"
CKAN_ROWS = 50
_CANARIAN = re.compile(
    r"canari|tenerife|gran canaria|lanzarote|fuerteventura|la palma|la gomera|el hierro|graciosa",
    re.IGNORECASE,
)
_REVIEW_STATUSES = frozenset({401, 403, 429})
NOTES = [
    "Sources found here are never enabled automatically: every positive needs a manual check "
    "of terms of use and data quality before it is added to sources.yaml.",
    "datos.gob.es is a national catalog; only datasets mentioning the Canary Islands are kept.",
]


def _record(endpoint: str, kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "status": None,
        "kind": kind,
        "items_found": 0,
        "needs_manual_check": False,
        **extra,
    }


def _failure(record: dict[str, Any], exc: FetchError) -> dict[str, Any]:
    status = getattr(exc, "status", None)
    record.update(status=status, error=f"{type(exc).__name__}: {exc}")
    record["needs_manual_check"] = status in _REVIEW_STATUSES
    return record


def _load_json(content: bytes) -> Any:
    """Parsed JSON, or None when the body is not JSON (a probe answered with something else)."""
    try:
        return json.loads(content)
    except ValueError:
        return None


def _text(value: Any) -> str:
    """Plain text of linked-data values: strings, ``{"_value": ...}`` or lists of those."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _text(value.get("_value"))
    if isinstance(value, list):
        return " ".join(_text(v) for v in value)
    return ""


def search_ckan(client: SafeHttpClient, base: str, term: str) -> dict[str, Any]:
    endpoint = f"{base}package_search?q={quote(term)}&rows={CKAN_ROWS}"
    record = _record(endpoint, "ckan_search", term=term)
    try:
        result = client.get(f"{base}package_search", params={"q": term, "rows": CKAN_ROWS})
        payload = _load_json(result.content)
    except FetchError as exc:
        return _failure(record, exc)
    record["status"] = result.status
    body = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(body, dict) or payload.get("success") is not True:
        record["error"] = "unexpected CKAN response"
        return record
    count = body.get("count")
    record["items_found"] = count if isinstance(count, int) else 0
    record["datasets"] = [
        {"name": d.get("name"), "title": d.get("title")}
        for d in body.get("results", [])[:10]
        if isinstance(d, dict)
    ]
    record["needs_manual_check"] = record["items_found"] > 0
    return record


def search_datos_gob_es(client: SafeHttpClient, term: str) -> dict[str, Any]:
    url = DATOS_GOB_ES_URL.format(term=quote(term))
    record = _record(f"{url}?_pageSize={CKAN_ROWS}", "datos_gob_es", term=term)
    try:
        result = client.get(url, params={"_pageSize": CKAN_ROWS})
        payload = _load_json(result.content)
    except FetchError as exc:
        return _failure(record, exc)
    record["status"] = result.status
    items = payload.get("result", {}).get("items", []) if isinstance(payload, dict) else []
    datasets = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"))
        if _CANARIAN.search(f"{title} {_text(item.get('description'))}"):
            datasets.append({"url": item.get("_about"), "title": title})
    record["items_found"] = len(datasets)
    record["datasets"] = datasets
    record["needs_manual_check"] = bool(datasets)
    return record


def _host_url(domain: str) -> str:
    text = domain.strip().rstrip("/")
    return text if re.match(r"https?://", text) else f"https://{text}"


def probe_domain(client: SafeHttpClient, domain: str) -> list[dict[str, Any]]:
    base = _host_url(domain)
    domain = base.split("://", 1)[1]
    results: list[dict[str, Any]] = []

    # --- The Events Calendar REST
    record = _record(
        f"{base}/wp-json/tribe/events/v1/events?per_page=1", "tribe_events", domain=domain
    )
    try:
        res = client.get(f"{base}/wp-json/tribe/events/v1/events", params={"per_page": 1})
        record["status"] = res.status
        payload = _load_json(res.content)
        if isinstance(payload, dict) and isinstance(payload.get("events"), list):
            total = payload.get("total")
            record["items_found"] = total if isinstance(total, int) else len(payload["events"])
            record["needs_manual_check"] = True
    except FetchError as exc:
        _failure(record, exc)
    results.append(record)

    # --- iCal feed
    record = _record(f"{base}/?ical=1", "ical", domain=domain)
    try:
        res = client.get(f"{base}/", params={"ical": 1})
        record["status"] = res.status
        ctype = res.headers.get("content-type", "").lower()
        body = res.content.lstrip(b"\xef\xbb\xbf \r\n\t")
        if ctype.startswith("text/calendar") or body.startswith(b"BEGIN:VCALENDAR"):
            record["items_found"] = res.content.count(b"BEGIN:VEVENT")
            record["needs_manual_check"] = True
    except FetchError as exc:
        _failure(record, exc)
    results.append(record)

    # --- Drupal JSON:API
    record = _record(f"{base}/jsonapi/node/event?page[limit]=1", "drupal_jsonapi", domain=domain)
    try:
        res = client.get(f"{base}/jsonapi/node/event", params={"page[limit]": 1})
        record["status"] = res.status
        payload = _load_json(res.content)
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            record["items_found"] = len(payload["data"])
            record["needs_manual_check"] = True
    except FetchError as exc:
        _failure(record, exc)
    results.append(record)
    return results


MAX_LINK_SOURCES = 10
LINKS_PER_SOURCE = 10


def configured_domains(sources: Sequence[SourceConfig]) -> set[str]:
    """Registrable domains of the configured sources: the only foreign domains links may use."""
    urls = [u for s in sources for u in (s.url, s.homepage) if u]
    return {registrable_domain(urlsplit(u).hostname or "") for u in urls}


def configured_urls(sources: Sequence[SourceConfig]) -> set[str]:
    urls: set[str] = set()
    for s in sources:
        urls.update(u for u in (s.url, s.homepage, *s.params.get("listing_urls", [])) if u)
    return urls


def discover_links(
    client: SafeHttpClient,
    sources: Sequence[SourceConfig],
    *,
    known: set[str],
    official_domains: set[str],
    max_sources: int = MAX_LINK_SOURCES,
    per_source: int = LINKS_PER_SOURCE,
) -> tuple[list[FoundCandidate], list[str]]:
    """Agenda-like links on the pages of enabled, configured sources (one page each).

    Bounded (``max_sources`` pages, ``per_source`` links), robots.txt respected, same
    registrable domain or ``official_domains`` only. Returns candidates and notes about
    pages that could not be read.
    """
    robots = RobotsPolicy(client)
    found: list[FoundCandidate] = []
    notes: list[str] = []
    seen = set(known)
    pages = [s for s in sources if s.enabled and (s.homepage or s.adapter == "web")]
    for cfg in pages[:max_sources]:
        page = cfg.homepage or cfg.url
        host = urlsplit(page).hostname or ""
        if not robots.allowed(page):
            notes.append(f"{cfg.id}: robots.txt does not allow {mask_secrets(page)}")
            continue
        try:
            response = client.get(page, allowed_hosts={host, f"www.{host.removeprefix('www.')}"})
        except FetchError as exc:
            notes.append(f"{cfg.id}: {exc}")
            continue
        if "html" not in response.headers.get("content-type", "").lower():
            continue
        html = response.content.decode("utf-8", errors="replace")
        links = extract_candidate_links(
            html, response.url, official_domains=official_domains, known=seen, limit=per_source
        )
        seen.update(links)
        found.extend(FoundCandidate(link, "web", f"link:{cfg.id}") for link in links)
    return found, notes


def run_discovery(
    client: SafeHttpClient,
    *,
    probes: Sequence[str],
    now: datetime,
    terms: Sequence[str] = DEFAULT_TERMS,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for base in CKAN_PORTALS:
        results.extend(search_ckan(client, base, term) for term in terms)
    results.extend(search_datos_gob_es(client, term) for term in terms)
    for domain in probes:
        results.extend(probe_domain(client, domain))
    return {
        "generated_at": now.isoformat(),
        "notes": NOTES,
        "results": results,
        "summary": {
            "requests": len(results),
            "needs_manual_check": sum(1 for r in results if r["needs_manual_check"]),
            "errors": sum(1 for r in results if "error" in r),
        },
    }
