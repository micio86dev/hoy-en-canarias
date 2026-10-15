"""Bluesky public AppView (``app.bsky.feed.getAuthorFeed``), no authentication.

Posts are not events: see :mod:`canarias_events.sources.social_posts` for the rules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from canarias_events.http import InvalidPayload
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceConfig,
    SourceFetchResult,
)
from canarias_events.sources.social_posts import PostEvents, SocialParams, SocialPost, as_aware

FEED_LIMIT = 30
POST_URL = "https://bsky.app/profile/{handle}/post/{rkey}"


def _created(value: object) -> datetime | None:
    try:
        return as_aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _links(post: dict[str, Any], record: dict[str, Any]) -> tuple[str, ...]:
    found: list[str] = []
    for facet in record.get("facets") or []:
        for feature in facet.get("features") or []:
            if str(feature.get("$type", "")).endswith("#link"):
                found.append(str(feature.get("uri", "")))
    for embed in (record.get("embed"), post.get("embed")):
        external = embed.get("external") if isinstance(embed, dict) else None
        if isinstance(external, dict):
            found.append(str(external.get("uri", "")))
    unique = dict.fromkeys(u for u in found if u.lower().startswith(("http://", "https://")))
    return tuple(unique)


class BlueskyAdapter(SourceAdapter):
    source_type = "bluesky"

    def __init__(self, config: SourceConfig) -> None:
        super().__init__(config)
        self.params = SocialParams.from_config(config)
        self.handle = self.params.handle

    def fetch(self, ctx: FetchContext) -> SourceFetchResult:
        config = self.config
        assert self.handle
        result = SourceFetchResult()
        payload = ctx.client.get_json(
            config.url,
            params={"actor": self.handle, "filter": "posts_no_replies", "limit": FEED_LIMIT},
        )
        feed = payload.get("feed") if isinstance(payload, dict) else None
        if not isinstance(feed, list):
            raise InvalidPayload(f"{config.id}: unexpected getAuthorFeed response")
        result.pages = 1
        posts: list[SocialPost] = []
        for item in feed:
            post = item.get("post") if isinstance(item, dict) else None
            if not isinstance(post, dict) or item.get("reason"):  # a repost is someone else's
                continue
            author = str((post.get("author") or {}).get("handle", ""))
            raw_record = post.get("record")
            record: dict[str, Any] = raw_record if isinstance(raw_record, dict) else {}
            uri = str(post.get("uri", ""))
            if author.lower() != self.handle.lower() or not uri:
                continue
            posts.append(
                SocialPost(
                    id=uri,
                    url=POST_URL.format(handle=author, rkey=uri.rsplit("/", 1)[-1]),
                    text=str(record.get("text", "")),
                    created_at=_created(record.get("createdAt")),
                    links=_links(post, record),
                )
            )
        PostEvents(
            config, ctx, result, params=self.params, source_type=self.source_type,
            skip_hosts=["bsky.app"],
        ).add_all(posts)  # fmt: skip
        return result
