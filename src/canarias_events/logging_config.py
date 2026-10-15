"""Text logging setup. URL secrets are masked by the filters installed in ``http``."""

from __future__ import annotations

import logging
import os
import sys
from typing import TextIO

import canarias_events.http  # noqa: F401  (installs the filter masking httpx request URLs)

LOG_LEVEL_ENV = "CANARIAS_LOG_LEVEL"
DEFAULT_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"


def configure_logging(level: str | None = None, *, stream: TextIO | None = None) -> None:
    """Configure the root logger; ``level`` wins over ``CANARIAS_LOG_LEVEL`` over INFO."""
    name = (level or os.environ.get(LOG_LEVEL_ENV) or DEFAULT_LEVEL).strip().upper()
    numeric = logging.getLevelName(name)
    if not isinstance(numeric, int):
        raise ValueError(f"invalid log level: {name}")

    root = logging.getLogger()
    root.handlers[:] = [h for h in root.handlers if not getattr(h, "_canarias", False)]
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, "%Y-%m-%dT%H:%M:%S%z"))
    handler._canarias = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(numeric)
    # Per-request transport chatter is only useful when debugging.
    quiet = logging.WARNING if numeric > logging.DEBUG else logging.NOTSET
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(quiet)
