"""Shared helpers for adapter tests: fixture loading and an offline HTTP client."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from canarias_events.date_range import TZ, DateWindow, explicit_window
from canarias_events.http import SafeHttpClient
from canarias_events.sources.base import FetchContext

FIXTURES = Path(__file__).parent / "fixtures"
PUBLIC_IP = "93.184.216.34"

# Next-week window used by the importer on 2026-10-15 (Mon 19 Oct to Sun 25 Oct inclusive).
WINDOW: DateWindow = explicit_window(date(2026, 10, 19), date(2026, 10, 25))
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=TZ)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> Any:
    return json.loads(fixture_bytes(name))


def _public_resolver(host: str) -> list[str]:
    return [PUBLIC_IP]


@pytest.fixture
def make_ctx() -> Callable[..., FetchContext]:
    """Build a FetchContext with an offline-friendly client (no sleeping, no retries)."""

    def factory(**client_kwargs: Any) -> FetchContext:
        client_kwargs.setdefault("resolver", _public_resolver)
        client_kwargs.setdefault("min_interval", 0.0)
        client_kwargs.setdefault("retries", 0)
        client_kwargs.setdefault("sleep", lambda _seconds: None)
        return FetchContext(
            client=SafeHttpClient(**client_kwargs),
            window=WINDOW,
            now=NOW,
            logger=logging.getLogger("test"),
            env={},
        )

    return factory
