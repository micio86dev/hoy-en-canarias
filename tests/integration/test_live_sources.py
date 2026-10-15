"""Opt-in checks against the real endpoints: ``uv run pytest -m live``.

Each enabled source is fetched once with the importer's real HTTP client. The tests only assert
structural health (no exception, sane counts); content changes daily.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

import pytest

from canarias_events.date_range import TZ, next_week
from canarias_events.http import SafeHttpClient
from canarias_events.sources.base import FetchContext, SourceConfig
from canarias_events.sources.registry import create_adapter, load_sources

CONFIG = Path(__file__).parent.parent.parent / "config" / "sources.yaml"
ENABLED = [s for s in load_sources(CONFIG) if s.enabled]

pytestmark = pytest.mark.live


@pytest.mark.parametrize("source", ENABLED, ids=lambda s: s.id)
def test_enabled_source_fetches_once(source: SourceConfig) -> None:
    now = datetime.now(TZ)
    with SafeHttpClient() as client:
        ctx = FetchContext(
            client=client,
            window=next_week(now),
            now=now,
            logger=logging.getLogger("live"),
            env=dict(os.environ),
        )
        result = create_adapter(source).fetch(ctx)

    assert (
        result.records_received == len(result.events) + len(result.discarded)
        or source.adapter == "ical"
    )
    if source.adapter == "ticketmaster" and not os.environ.get(source.auth_env or ""):
        assert result.warnings and result.pages == 0
        return
    assert result.pages >= 1
    for event in result.events:
        assert event.title and event.source_name and event.source_type == source.adapter
