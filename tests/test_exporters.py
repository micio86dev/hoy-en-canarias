from __future__ import annotations

import csv
import json
import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from canarias_events.database import Database
from canarias_events.exporters import (
    build_sources_report,
    export_all,
    write_atomic,
)
from canarias_events.http import SafeHttpClient
from canarias_events.sources.base import SourceConfig
from canarias_events.sources.registry import CatalogEntry, SourcesCatalog
from tests.conftest import NOW, WINDOW
from tests.test_database import make_cfg
from tests.test_pipeline import FakeAdapter, ev, run


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "x.sqlite")
    yield database
    database.close()


@pytest.fixture
def client() -> Iterator[SafeHttpClient]:
    http = SafeHttpClient(resolver=lambda _h: ["93.184.216.34"], min_interval=0, retries=0)
    yield http
    http.close()


def populated(db: Database, client: SafeHttpClient) -> dict[str, Any]:
    rich = ev(
        "a", "1", "Año Nuevo: «Fiesta» en La Laguna", municipality="San Cristóbal de La Laguna"
    )
    rich = rich.model_copy(
        update={
            "tags": ["música", "gratis"],
            "description": 'Línea 1\nLínea "2", con coma',
            "raw_data": {"orig": {"id": 1}},
            "official_event_url": "https://club.test/e/1",
        }
    )
    dup = ev("b", "7", "AÑO NUEVO Fiesta en La Laguna", municipality="San Cristóbal de La Laguna")
    outside = ev("a", "2", "Pasado", day=3)
    adapters = {
        "a": FakeAdapter(make_cfg("a"), [rich, outside]),
        "b": FakeAdapter(make_cfg("b"), [dup]),
    }
    return run(db, client, adapters)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def catalog() -> SourcesCatalog:
    return SourcesCatalog(
        sources=[make_cfg("a"), make_cfg("b")],
        candidates=[CatalogEntry(name="Cand", url="https://c.test", reason="HTML only",
                                 checked_at=date(2026, 10, 15))],
        excluded=[CatalogEntry(name="Excl", reason="No API", checked_at=date(2026, 10, 15))],
    )  # fmt: skip


# --------------------------------------------------------------------------- atomic write


def test_write_atomic_replaces_and_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "out" / "f.txt"
    write_atomic(target, b"one")
    write_atomic(target, b"two")
    assert target.read_bytes() == b"two"
    assert [p.name for p in target.parent.iterdir()] == ["f.txt"]


def test_write_atomic_keeps_old_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "f.txt"
    write_atomic(target, b"old")

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_atomic(target, b"new")
    assert target.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]


# --------------------------------------------------------------------------- json / csv


def test_events_json_has_only_window_events_with_sources(
    db: Database, client: SafeHttpClient, tmp_path: Path
) -> None:
    report = populated(db, client)
    paths = export_all(db, catalog(), WINDOW, report, tmp_path / "out", now=NOW, env={})
    raw_text = paths["events"].read_text(encoding="utf-8")
    assert "Año Nuevo" in raw_text  # ensure_ascii is off
    data = json.loads(raw_text)
    assert len(data) == 1  # the 3 Oct event is outside the window; the duplicate was merged
    event = data[0]
    assert event["title"] == "Año Nuevo: «Fiesta» en La Laguna"
    assert "raw_data" not in event
    sources = {s["source_id"]: s for s in event["sources"]}
    assert set(sources) == {"a", "b"}
    assert sources["a"]["source_name"] == "Source a"
    assert sources["a"]["source_event_id"] == "1"
    assert sources["a"]["official_event_url"] == "https://club.test/e/1"
    assert sources["a"]["match_method"] == "source_id"
    assert sources["b"]["match_method"] == "fuzzy"
    assert sources["a"]["raw_data"] == {"orig": {"id": 1}}
    assert event["start_datetime"].startswith("2026-10-21T20:00:00")


def test_events_csv_is_excel_friendly_and_roundtrips(
    db: Database, client: SafeHttpClient, tmp_path: Path
) -> None:
    report = populated(db, client)
    paths = export_all(db, catalog(), WINDOW, report, tmp_path / "out", now=NOW, env={})
    raw = paths["csv"].read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw
    assert raw.endswith(b'"\r\n')
    with paths["csv"].open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Año Nuevo: «Fiesta» en La Laguna"
    assert row["tags"] == "música|gratis"
    assert row["description"] == 'Línea 1\nLínea "2", con coma'
    assert row["sources"] == "Source a|Source b"
    assert row["source_event_ids"] == "1|7"
    assert "raw_data" not in row
    assert raw.decode("utf-8-sig").splitlines()[1].startswith('"')  # QUOTE_ALL


def test_reports_are_written(db: Database, client: SafeHttpClient, tmp_path: Path) -> None:
    report = populated(db, client)
    out = tmp_path / "out"
    paths = export_all(db, catalog(), WINDOW, report, out, now=NOW, env={})
    assert read_json(paths["import_report"])["run_id"] == report["run_id"]
    assert set(paths) == {"events", "csv", "import_report", "sources_report"}
    assert {p.name for p in out.iterdir()} == {
        "events.json", "events.csv", "import-report.json", "sources-report.json"
    }  # fmt: skip


def test_export_with_island_filter(db: Database, client: SafeHttpClient, tmp_path: Path) -> None:
    adapters = {
        "a": FakeAdapter(
            make_cfg("a"),
            [ev("a", "1", "Uno"), ev("a", "2", "Dos otra", island="La Palma", municipality=None)],
        )
    }
    report = run(db, client, adapters)
    paths = export_all(
        db, catalog(), WINDOW, report, tmp_path / "o", now=NOW, env={}, island="La Palma"
    )
    titles = [e["title"] for e in read_json(paths["events"])]
    assert titles == ["Dos otra"]


# --------------------------------------------------------------------------- sources report


def test_sources_report_statuses_and_sections(db: Database, client: SafeHttpClient) -> None:
    configs: list[SourceConfig] = [
        make_cfg("ok", license="CC-BY", notes="main"),
        make_cfg("off", enabled=False),
        make_cfg("old", enabled=False, stale=True),
        make_cfg("tm", auth_env="TM_KEY"),
        make_cfg("keyed", auth_env="K"),
    ]
    cat = SourcesCatalog(
        sources=configs,
        candidates=catalog().candidates,
        excluded=catalog().excluded,
    )
    db.sync_sources(configs)
    run_id = db.start_run(NOW, NOW, NOW, {})
    db.record_source_result("ok", run_id, error=None, now=NOW)
    db.record_source_result("keyed", run_id, error="HTTP 500", now=NOW)

    report = build_sources_report(cat, db, NOW, env={"K": "present"})
    entries = {e["id"]: e for e in report["sources"]}
    assert entries["ok"]["status"] == "integrated"
    assert entries["ok"]["last_result"]["last_success_at"] == NOW.isoformat()
    assert entries["ok"]["license"] == "CC-BY"
    assert entries["ok"]["notes"] == "main"
    assert entries["ok"]["auth"] == {"required": False, "env": None, "configured": None}
    assert entries["off"]["status"] == "disabled"
    assert entries["old"]["status"] == "stale"
    assert entries["tm"]["status"] == "skipped"
    assert entries["tm"]["auth"] == {"required": True, "env": "TM_KEY", "configured": False}
    assert entries["keyed"]["status"] == "integrated"
    assert entries["keyed"]["auth"]["configured"] is True
    assert entries["keyed"]["last_result"]["last_error"] == "HTTP 500"
    assert report["candidates"] == [
        {"name": "Cand", "url": "https://c.test", "reason": "HTML only", "checked_at": "2026-10-15"}
    ]
    assert report["excluded"][0]["name"] == "Excl"
    assert report["generated_at"] == NOW.isoformat()


def test_sources_report_carries_the_health_summary(db: Database, client: SafeHttpClient) -> None:
    cfg = make_cfg("ok")
    run(db, client, {"ok": FakeAdapter(cfg, [ev("ok", "1", "Concierto")])})
    run(db, client, {"ok": FakeAdapter(cfg, error=RuntimeError("down"))})
    report = build_sources_report(SourcesCatalog(sources=[cfg]), db, NOW, env={})
    health = report["sources"][0]["health"]
    assert health["runs_considered"] == 2
    assert health["consecutive_failures"] == 1
    assert health["error_rate"] == 0.5
    assert health["last_events_found"] == 1
    assert health["last_status"] == "failed"
