"""Mastodon public account RSS (``https://<instance>/@<user>.rss``), no authentication.

Posts are not events: see :mod:`canarias_events.sources.social_posts` for the rules.
"""

from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import feedparser

from canarias_events.extract.util import parse_html
from canarias_events.http import InvalidPayload
from canarias_events.normalize import html_to_text
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceConfig,
    SourceFetchResult,
    clean_text,
)
from canarias_events.sources.social_posts import PostEvents, SocialParams, SocialPost, as_aware


def _published(entry: Any) -> datetime | None:
    try:
        return as_aware(parsedate_to_datetime(str(entry.get("published"))))
    except (TypeError, ValueError):
        return None


def _links(html: str) -> tuple[str, ...]:
    """Outgoing links of a post; hashtag and mention links are Mastodon's own."""
    root = parse_html(html)
    if root is None:
        return ()
    found: list[str] = []
    for anchor in root.xpath("//a[@href]"):
        css = f"{anchor.get('class') or ''} {anchor.get('rel') or ''}"
        href = (anchor.get("href") or "").strip()
        if (
            "hashtag" in css
            or "mention" in css
            or not href.lower().startswith(("http://", "https://"))
        ):
            continue
        if href not in found:
            found.append(href)
    return tuple(found)


class MastodonAdapter(SourceAdapter):
    source_type = "mastodon"

    def __init__(self, config: SourceConfig) -> None:
        super().__init__(config)
        self.params = SocialParams.from_config(config)

    def fetch(self, ctx: FetchContext) -> SourceFetchResult:
        config = self.config
        result = SourceFetchResult()
        parsed = feedparser.parse(ctx.client.get(config.url).content)
        if not parsed.version:  # feedparser leaves it empty for anything but RSS/Atom
            raise InvalidPayload(f"{config.id}: response is not an RSS/Atom feed")
        result.pages = 1
        posts: list[SocialPost] = []
        for entry in parsed.entries:
            link = clean_text(entry.get("link"))
            html = str(entry.get("summary") or entry.get("title") or "")
            post_id = clean_text(entry.get("id")) or link
            if post_id is None:
                result.records_received += 1
                result.discard("missing id", None)
                continue
            posts.append(
                SocialPost(
                    id=post_id, url=link, text=html_to_text(html), created_at=_published(entry),
                    links=_links(html),
                )
            )  # fmt: skip
        host = urlsplit(config.url).hostname or ""
        PostEvents(
            config, ctx, result, params=self.params, source_type=self.source_type, skip_hosts=[host]
        ).add_all(posts)
        return result
