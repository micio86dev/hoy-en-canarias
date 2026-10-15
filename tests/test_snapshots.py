"""Optional diagnostic snapshots of failed or anomalous listings, with retention."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from canarias_events.crawl_settings import CrawlSettings, SnapshotSettings
from canarias_events.snapshots import prune_snapshots, save_snapshot
from tests.conftest import NOW


def settings(**kw: object) -> SnapshotSettings:
    return SnapshotSettings(enabled=True, **kw)


def test_snapshots_are_off_by_default() -> None:
    assert CrawlSettings().snapshots.enabled is False


def test_save_writes_under_the_source_directory(tmp_path: Path) -> None:
    path = save_snapshot(tmp_path, "my_source", "<html>hi</html>", NOW, settings())
    assert path is not None
    assert path.parent == tmp_path / "my_source" and path.suffix == ".html"
    assert path.read_text(encoding="utf-8") == "<html>hi</html>"
    assert path.name.startswith("20261015T120000")


def test_save_is_size_capped_and_marks_the_cut(tmp_path: Path) -> None:
    path = save_snapshot(tmp_path, "s", "é" * 1000, NOW, settings(max_bytes=100))
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) <= 100 + len(b"\n<!-- truncated -->")
    assert text.endswith("<!-- truncated -->")


def test_two_snapshots_in_the_same_second_do_not_overwrite(tmp_path: Path) -> None:
    first = save_snapshot(tmp_path, "s", "one", NOW, settings())
    second = save_snapshot(tmp_path, "s", "two", NOW, settings())
    assert first != second and first is not None and second is not None
    assert first.read_text(encoding="utf-8") == "one"


def test_source_ids_cannot_escape_the_snapshot_directory(tmp_path: Path) -> None:
    path = save_snapshot(tmp_path / "snaps", "../../evil", "x", NOW, settings())
    assert path is not None and (tmp_path / "snaps") in path.parents


def test_retention_by_age(tmp_path: Path) -> None:
    old = save_snapshot(tmp_path, "s", "old", NOW - timedelta(days=20), settings())
    fresh = save_snapshot(tmp_path, "s", "fresh", NOW - timedelta(days=2), settings())
    assert old is not None and fresh is not None
    assert prune_snapshots(tmp_path, settings(retention_days=14), NOW) == 1
    assert not old.exists() and fresh.exists()


def test_retention_by_count_keeps_the_newest(tmp_path: Path) -> None:
    paths = [
        save_snapshot(tmp_path, "s", str(n), NOW - timedelta(hours=n), settings()) for n in range(5)
    ]
    other = save_snapshot(tmp_path, "t", "x", NOW, settings())
    assert prune_snapshots(tmp_path, settings(max_files=2), NOW) == 3
    kept = sorted(p.name for p in (tmp_path / "s").iterdir())
    assert kept == sorted(p.name for p in paths[:2] if p)
    assert other is not None and other.exists()


def test_prune_never_touches_foreign_files(tmp_path: Path) -> None:
    (tmp_path / "s").mkdir()
    note = tmp_path / "s" / "notes.txt"
    note.write_text("keep", encoding="utf-8")
    stray = tmp_path / "README.md"
    stray.write_text("keep", encoding="utf-8")
    assert prune_snapshots(tmp_path, settings(retention_days=0, max_files=1), NOW) == 0
    assert note.exists() and stray.exists()
    assert prune_snapshots(tmp_path / "missing", settings(), NOW) == 0
