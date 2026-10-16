from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from hoy_api.deps import ClockDep, SessionDep, enforce_rate_limit
from hoy_api.repositories.events import sitemap_entries
from hoy_api.time_windows import local_today

router = APIRouter(dependencies=[Depends(enforce_rate_limit)], tags=["seo"])


class SitemapEntry(BaseModel):
    slug: str
    lastmod: datetime


class SitemapData(BaseModel):
    items: list[SitemapEntry]


@router.get("/sitemap-data", response_model=SitemapData)
def sitemap_data(session: SessionDep, now: ClockDep) -> SitemapData:
    """Slugs and last-update times for the sitemap builder in the web app (read-only)."""
    rows = sitemap_entries(session, local_today(now))
    return SitemapData(items=[SitemapEntry(slug=slug, lastmod=updated) for slug, updated in rows])
