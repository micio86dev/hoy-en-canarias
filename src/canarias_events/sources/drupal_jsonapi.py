"""Adapter for the Hola Islas Canarias Drupal JSON:API (``/jsonapi/node/event``).

The field names (``field_e_start_date``, ``field_t_islands``...) are specific to this site and
were verified against the live API on 2026-10-15.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import urlsplit

from canarias_events.geo import normalize_municipality
from canarias_events.http import InvalidPayload, same_host_as
from canarias_events.normalize import html_to_text, parse_datetime_any
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceFetchResult,
    clean_text,
    resolve_island,
)

DEFAULT_MAX_PAGES = 20
PAGE_LIMIT = 50
EVENT_FIELDS = (
    "title,path,changed,field_event_city,field_event_description,field_event_free,"
    "field_event_latitude,field_event_longitude,field_event_tickets_website,field_event_website,"
    "field_e_start_date,field_e_end_date,field_e_calculated_end_date,field_t_islands,"
    "field_t_municipality,field_event_category"
)
INCLUDE = "field_t_islands,field_t_municipality,field_event_category"

Included = dict[tuple[str, str], str]


def _as_float(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _link_uri(value: object) -> str | None:
    return clean_text(value.get("uri")) if isinstance(value, dict) else None


def _day(value: object) -> date | None:
    moment = parse_datetime_any(value)
    return moment.day if moment else None


class DrupalJsonApiAdapter(SourceAdapter):
    source_type = "drupal_jsonapi"

    def fetch(self, ctx: FetchContext) -> SourceFetchResult:
        config = self.config
        result = SourceFetchResult()
        parts = urlsplit(config.url)
        origin = f"{parts.scheme}://{parts.netloc}"
        max_pages = int(config.params.get("max_pages", DEFAULT_MAX_PAGES))
        url: str | None = config.url
        params: dict[str, Any] | None = {
            "filter[s][condition][path]": "field_e_start_date",
            "filter[s][condition][operator]": "<",
            "filter[s][condition][value]": ctx.window.end.date().isoformat(),
            "filter[e][condition][path]": "field_e_calculated_end_date",
            "filter[e][condition][operator]": ">=",
            "filter[e][condition][value]": ctx.window.start_date.isoformat(),
            "sort": "field_e_start_date",
            "page[limit]": PAGE_LIMIT,
            "include": INCLUDE,
            "fields[node--event]": EVENT_FIELDS,
            "fields[taxonomy_term--islands]": "name",
            "fields[taxonomy_term--event_category]": "name",
        }
        while url is not None:
            payload = ctx.client.get_json(url, params=params, allowed_hosts={parts.hostname or ""})
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise InvalidPayload(f"{config.id}: response has no 'data' list")
            result.pages += 1
            included = self._index(payload.get("included"))
            for item in payload["data"]:
                result.records_received += 1
                if isinstance(item, dict):
                    self._map(item, included, origin, result)
                else:
                    result.discard("record is not an object", None)
            url, params = self._next(payload, result, max_pages), None
        return result

    def _next(
        self, payload: dict[str, Any], result: SourceFetchResult, max_pages: int
    ) -> str | None:
        links = payload.get("links")
        nxt = links.get("next") if isinstance(links, dict) else None
        href = nxt.get("href") if isinstance(nxt, dict) else None
        if not isinstance(href, str) or not href:
            return None
        if not same_host_as(self.config.url, href):
            result.warnings.append(f"ignored links.next on a different host: {href}")
            return None
        if result.pages >= max_pages:
            result.warnings.append(f"stopped at max_pages={max_pages}; more pages are available")
            return None
        return href

    @staticmethod
    def _index(included: object) -> Included:
        index: Included = {}
        if isinstance(included, list):
            for term in included:
                if not isinstance(term, dict):
                    continue
                attributes = term.get("attributes")
                name = clean_text(attributes.get("name")) if isinstance(attributes, dict) else None
                if name and isinstance(term.get("type"), str) and isinstance(term.get("id"), str):
                    index[(term["type"], term["id"])] = name
        return index

    @staticmethod
    def _term_names(item: dict[str, Any], field: str, included: Included) -> list[str]:
        relationships = item.get("relationships")
        rel = relationships.get(field) if isinstance(relationships, dict) else None
        data = rel.get("data") if isinstance(rel, dict) else None
        refs = data if isinstance(data, list) else [data]
        names: list[str] = []
        for ref in refs:
            if isinstance(ref, dict):
                name = included.get((str(ref.get("type")), str(ref.get("id"))))
                if name and name not in names:
                    names.append(name)
        return names

    # ------------------------------------------------------------------ mapping

    def _map(
        self, item: dict[str, Any], included: Included, origin: str, result: SourceFetchResult
    ) -> None:
        config = self.config
        source_id = clean_text(item.get("id"))
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            result.discard("record has no attributes", source_id)
            return
        title = html_to_text(clean_text(attributes.get("title")))
        if not title:
            result.discard("missing title", source_id)
            return
        start = _day(attributes.get("field_e_start_date"))
        if start is None:
            result.discard("missing start date", source_id)
            return
        end = _day(attributes.get("field_e_end_date"))

        islands = self._term_names(item, "field_t_islands", included)
        municipalities = self._term_names(item, "field_t_municipality", included)
        categories = self._term_names(item, "field_event_category", included)
        city = clean_text(attributes.get("field_event_city"))
        municipality = (
            normalize_municipality(municipalities[0])
            if len(municipalities) == 1
            else (normalize_municipality(city) if not municipalities else None)
        )
        latitude = _as_float(attributes.get("field_event_latitude"))
        longitude = _as_float(attributes.get("field_event_longitude"))
        island = resolve_island(
            config,
            island=islands[0] if len(islands) == 1 else None,
            municipality=municipality,
            latitude=latitude if len(islands) != 1 else None,
            longitude=longitude if len(islands) != 1 else None,
        )
        if len(islands) > 1:
            island = None  # several islands declared: do not pick one
        free = str(attributes.get("field_event_free")).strip() == "1"
        alias = attributes.get("path")
        alias_path = clean_text(alias.get("alias")) if isinstance(alias, dict) else None
        extra: dict[str, Any] = {}
        if len(islands) > 1:
            extra["islands"] = islands
        if len(municipalities) > 1:
            extra["municipalities"] = municipalities
        if city:
            extra["event_city"] = city
        website = _link_uri(attributes.get("field_event_website"))
        if website:
            extra["event_website"] = website

        result.add_event(
            source_id,
            title=title,
            description=html_to_text(clean_text(attributes.get("field_event_description"))) or None,
            category=categories[0] if categories else None,
            tags=categories[1:],
            start_date=start,
            end_date=end,
            all_day=True,
            municipality=municipality,
            island=island,
            latitude=latitude,
            longitude=longitude,
            price_min=0.0 if free else None,
            price_max=0.0 if free else None,
            is_free=True if free else None,
            ticket_url=_link_uri(attributes.get("field_event_tickets_website")),
            official_event_url=f"{origin}{alias_path}" if alias_path else None,
            source_name=config.name,
            source_url=config.url,
            source_event_id=source_id,
            source_type=self.source_type,
            source_license=config.license,
            raw_data=item,
            extra=extra,
        )
