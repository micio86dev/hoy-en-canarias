"""Adapter for the Hola Islas Canarias Drupal JSON:API (``/jsonapi/node/event``).

The field names (``field_e_start_date``, ``field_t_islands``...) are specific to this site and
were verified against the live API on 2026-10-15.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urljoin, urlsplit

from canarias_events.geo import normalize_municipality
from canarias_events.http import InvalidPayload, mask_secrets, same_host_as
from canarias_events.normalize import html_to_text, parse_datetime_any
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceFetchResult,
    as_float,
    clean_text,
    resolve_island,
)

DEFAULT_MAX_PAGES = 20
PAGE_LIMIT = 50
EVENT_FIELDS = (
    "title,path,changed,field_event_city,field_event_description,field_event_free,"
    "field_event_latitude,field_event_longitude,field_event_tickets_website,field_event_website,"
    "field_e_start_date,field_e_end_date,field_e_calculated_end_date,field_t_islands,"
    "field_t_municipality,field_event_category,field_t_tags,field_h_images,field_h_image_gallery"
)
INCLUDE = (
    "field_t_islands,field_t_municipality,field_event_category,field_t_tags,"
    "field_h_images,field_h_images.field_media_image,"
    "field_h_image_gallery,field_h_image_gallery.field_media_image"
)
IMAGE_FIELDS = (("field_h_images", "header"), ("field_h_image_gallery", "gallery"))

Ref = tuple[str, str]


@dataclass
class Included:
    """Lookup tables built from the ``included`` section of one JSON:API page."""

    names: dict[Ref, str] = field(default_factory=dict)  # taxonomy term -> name
    media_files: dict[Ref, dict[str, Any]] = field(default_factory=dict)  # media -> image ref
    file_urls: dict[Ref, str] = field(default_factory=dict)  # file--file -> site-relative url


def _link_uri(value: object) -> str | None:
    return clean_text(value.get("uri")) if isinstance(value, dict) else None


def _refs(relationships: object, field_name: str) -> list[dict[str, Any]]:
    """Resource identifiers of a relationship that may be an object, a list or null."""
    rel = relationships.get(field_name) if isinstance(relationships, dict) else None
    data = rel.get("data") if isinstance(rel, dict) else None
    return [ref for ref in (data if isinstance(data, list) else [data]) if isinstance(ref, dict)]


def _first_ref(relationships: object, field_name: str) -> dict[str, Any] | None:
    refs = _refs(relationships, field_name)
    return refs[0] if refs else None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


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
            "fields[taxonomy_term--tags]": "name",
            "fields[media--header_images]": "name,field_media_image",
            "fields[media--list_image]": "name,field_media_image",
            "fields[file--file]": "uri",
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
            result.warnings.append(f"ignored links.next on a different host: {mask_secrets(href)}")
            return None
        if result.pages >= max_pages:
            result.warnings.append(f"stopped at max_pages={max_pages}; more pages are available")
            return None
        return href

    @staticmethod
    def _index(included: object) -> Included:
        index = Included()
        if not isinstance(included, list):
            return index
        for entry in included:
            if not isinstance(entry, dict):
                continue
            kind, ident = entry.get("type"), entry.get("id")
            if not isinstance(kind, str) or not isinstance(ident, str):
                continue
            attributes = entry.get("attributes")
            attributes = attributes if isinstance(attributes, dict) else {}
            if kind == "file--file":
                uri = attributes.get("uri")
                url = clean_text(uri.get("url")) if isinstance(uri, dict) else None
                if url:
                    index.file_urls[(kind, ident)] = url
            elif kind.startswith("media--"):
                ref = _first_ref(entry.get("relationships"), "field_media_image")
                if ref:
                    index.media_files[(kind, ident)] = ref
            elif name := clean_text(attributes.get("name")):
                index.names[(kind, ident)] = name
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
                name = included.names.get((str(ref.get("type")), str(ref.get("id"))))
                if name and name not in names:
                    names.append(name)
        return names

    @staticmethod
    def _images(item: dict[str, Any], included: Included, origin: str) -> list[dict[str, Any]]:
        """Images published for the event, header first; unresolvable media are skipped."""
        images: list[dict[str, Any]] = []
        seen: set[str] = set()
        for field_name, role in IMAGE_FIELDS:
            for media in _refs(item.get("relationships"), field_name):
                file_ref = included.media_files.get((str(media.get("type")), str(media.get("id"))))
                path = file_ref and included.file_urls.get(
                    (str(file_ref.get("type")), str(file_ref.get("id")))
                )
                url = urljoin(origin + "/", path) if path else None
                if not url or not url.startswith(("http://", "https://")) or url in seen:
                    continue
                seen.add(url)
                meta = file_ref.get("meta") if file_ref else None
                meta = meta if isinstance(meta, dict) else {}
                images.append(
                    {
                        "url": url,
                        "alt": clean_text(meta.get("alt")),
                        "width": _int_or_none(meta.get("width")),
                        "height": _int_or_none(meta.get("height")),
                        "role": role,
                    }
                )
        return images

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
        tags = [t for t in self._term_names(item, "field_t_tags", included) if t not in categories]
        images = self._images(item, included, origin)
        city = clean_text(attributes.get("field_event_city"))
        municipality = (
            normalize_municipality(municipalities[0])
            if len(municipalities) == 1
            else (normalize_municipality(city) if not municipalities else None)
        )
        latitude = as_float(attributes.get("field_event_latitude"))
        longitude = as_float(attributes.get("field_event_longitude"))
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

        if images:
            extra["images"] = images

        result.add_event(
            source_id,
            title=title,
            description=html_to_text(clean_text(attributes.get("field_event_description"))) or None,
            category=categories[0] if categories else None,
            tags=[*categories[1:], *(t for t in tags if t not in categories[1:])],
            image_url=images[0]["url"] if images else None,
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
