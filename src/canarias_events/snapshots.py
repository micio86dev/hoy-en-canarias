"""Optional diagnostic snapshots: the raw listing of a failed or anomalous run, with retention.

Files are ``<root>/<source>/<YYYYmmddTHHMMSS>[-n].html``. Retention only ever deletes files that
match this naming, so nothing else in the directory is touched.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from canarias_events.crawl_settings import SnapshotSettings

logger = logging.getLogger("canarias_events.snapshots")

_STAMP = "%Y%m%dT%H%M%S"
_RE_NAME = re.compile(r"^(\d{8}T\d{6})(?:-\d+)?\.html$")
_RE_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_MARK = "\n<!-- truncated -->"


def _directory(root: Path, source_id: str) -> Path:
    safe = _RE_UNSAFE.sub("_", source_id).strip("._") or "source"
    return root / safe


def save_snapshot(
    root: Path, source_id: str, text: str, now: datetime, settings: SnapshotSettings
) -> Path | None:
    """Write ``text`` (cut at ``max_bytes``); None when the file cannot be written."""
    data = text.encode("utf-8")
    if len(data) > settings.max_bytes:
        data = data[: settings.max_bytes].decode("utf-8", errors="ignore").encode("utf-8")
        data += _MARK.encode("utf-8")
    folder = _directory(root, source_id)
    stamp = now.strftime(_STAMP)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{stamp}.html"
        counter = 1
        while path.exists():
            path = folder / f"{stamp}-{counter}.html"
            counter += 1
        path.write_bytes(data)
    except OSError as exc:  # diagnostics must never fail the import
        logger.warning("snapshot for %s not saved: %s", source_id, exc)
        return None
    return path


def prune_snapshots(root: Path, settings: SnapshotSettings, now: datetime) -> int:
    """Delete snapshots older than ``retention_days`` and beyond ``max_files`` per source."""
    if not root.is_dir():
        return 0
    cutoff = now.replace(tzinfo=None) - timedelta(days=settings.retention_days)
    removed = 0
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        dated: list[tuple[datetime, str, Path]] = []
        for path in folder.iterdir():
            found = _RE_NAME.match(path.name)
            if found and path.is_file():
                dated.append((datetime.strptime(found.group(1), _STAMP), path.name, path))
        dated.sort(reverse=True)  # newest first
        for index, (when, _name, path) in enumerate(dated):
            if when < cutoff or index >= settings.max_files:
                try:
                    path.unlink()
                    removed += 1
                except OSError as exc:
                    logger.warning("snapshot %s not removed: %s", path, exc)
    return removed
