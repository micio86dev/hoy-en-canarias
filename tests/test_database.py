from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from canarias_events.database import _MIGRATIONS, Database, SqliteHttpCache, default_db_path
from canarias_events.date_range import TZ
from canarias_events.http import SafeHttpClient
from canarias_events.models import Event
from canarias_events.sources.base import SourceConfig

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=TZ)


def make_cfg(source_id: str = "s1", **overrides: object) -> SourceConfig:
    data: dict[str, object] = {
        "id": source_id,
        "name": f"Source {source_id}",
        "adapter": "tribe_events",
        "url": f"https://{source_id}.test/wp-json/tribe/events/v1/events",
    }
    data.update(overrides)
    return SourceConfig.model_validate(data)


def test_creates_parent_dir_and_configures_pragmas(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "events.sqlite"
    with Database(path) as db:
        assert path.exists()
        assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.schema_version == len(_MIGRATIONS)
        tables = {
            r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {
        "sources", "events", "event_sources", "import_runs", "possible_duplicates", "http_cache"
    } <= tables  # fmt: skip


def test_default_path_comes_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CANARIAS_DB_PATH", raising=False)
    assert default_db_path() == Path("data/events.sqlite")
    monkeypatch.setenv("CANARIAS_DB_PATH", str(tmp_path / "x.sqlite"))
    assert default_db_path() == tmp_path / "x.sqlite"
    with Database() as db:
        assert db.path == tmp_path / "x.sqlite"


def test_reopening_keeps_schema_version_and_data(tmp_path: Path) -> None:
    path = tmp_path / "e.sqlite"
    with Database(path) as db:
        db.sync_sources([make_cfg()])
    with Database(path) as db:
        assert db.schema_version == len(_MIGRATIONS)
        assert db.count("sources") == 1


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "e.sqlite"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 99")
    raw.commit()
    raw.close()
    with pytest.raises(RuntimeError, match="newer"):
        Database(path)


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    with Database(tmp_path / "e.sqlite") as db, pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO event_sources (event_id, source_id, source_event_id, raw_data,"
            " content_hash, first_seen_at, last_seen_at, field_provenance, match_method)"
            " VALUES ('nope', 'nope', 'x', '{}', 'h', 'a', 'b', '[]', 'source_id')"
        )


def test_sync_sources_updates_config_but_keeps_results(tmp_path: Path) -> None:
    with Database(tmp_path / "e.sqlite") as db:
        db.sync_sources([make_cfg(priority=70)])
        run = db.start_run(NOW, NOW, NOW, {})
        db.record_source_result("s1", run, error=None, now=NOW)
        db.sync_sources([make_cfg(name="Renamed", priority=20, enabled=False)])
        row = db.source_rows()["s1"]
        assert row["name"] == "Renamed"
        assert row["enabled"] == 0
        assert row["priority"] == 20
        assert row["last_success_at"] == NOW.isoformat()
        assert row["last_run_id"] == run
        db.record_source_result("s1", run, error="boom", now=NOW)
        assert db.source_rows()["s1"]["last_error"] == "boom"
        assert db.priority_of("unknown") == 50


def test_import_run_roundtrip(tmp_path: Path) -> None:
    with Database(tmp_path / "e.sqlite") as db:
        assert db.get_run() is None
        first = db.start_run(NOW, NOW, NOW, {"island": "Tenerife"})
        second = db.start_run(NOW, NOW, NOW, {})
        db.finish_run(second, NOW, {"totals": {"events": 3}})
        latest = db.get_run()
        assert latest is not None
        assert latest.id == second
        assert latest.report == {"totals": {"events": 3}}
        older = db.get_run(first)
        assert older is not None
        assert older.params == {"island": "Tenerife"}
        assert older.report is None


def test_sqlite_http_cache_roundtrip_and_masks_secret_keys(tmp_path: Path) -> None:
    with Database(tmp_path / "e.sqlite") as db:
        cache = SqliteHttpCache(db)
        assert cache.get("https://a.test/x") is None
        cache.set("https://a.test/x?apikey=SECRET", '"v1"', "Mon", b"body")
        cache.set("https://a.test/x?apikey=SECRET", '"v2"', None, b"body2")
        assert cache.get("https://a.test/x?apikey=SECRET") == ('"v2"', None, b"body2")
        keys = [r[0] for r in db.conn.execute("SELECT url FROM http_cache")]
        assert len(keys) == 1
        assert "SECRET" not in keys[0]


def test_sqlite_cache_drives_conditional_requests(tmp_path: Path) -> None:
    import httpx
    import respx

    with Database(tmp_path / "e.sqlite") as db, respx.mock:
        route = respx.get("https://a.test/feed").mock(
            side_effect=[
                httpx.Response(200, headers={"ETag": '"abc"'}, content=b"payload"),
                httpx.Response(304),
            ]
        )
        client = SafeHttpClient(
            resolver=lambda _h: ["93.184.216.34"], min_interval=0, retries=0,
            cache=SqliteHttpCache(db), sleep=lambda _s: None,
        )  # fmt: skip
        assert client.get("https://a.test/feed").content == b"payload"
        second = client.get("https://a.test/feed")
        assert second.from_cache
        assert second.content == b"payload"
        assert route.calls.last.request.headers["if-none-match"] == '"abc"'


# --------------------------------------------------------------------------- quarantine


def _quarantine_event(title: str) -> Event:
    return Event(
        title=title,
        start_datetime=datetime(2026, 10, 21, 20, 0, tzinfo=TZ),
        island="Tenerife",
        source_name="Source q",
        source_event_id=title,
        raw_data={"k": "v"},
    )


def test_quarantine_roundtrip_keeps_the_event_intact() -> None:
    with Database(":memory:") as db:
        event = _quarantine_event("Concierto")
        db.quarantine_add("q", 7, "count_drop", [event], NOW)
        [item] = db.quarantine_pending("q")
        assert (item.source_id, item.run_id, item.reason) == ("q", 7, "count_drop")
        assert item.event == event
        assert db.count("events") == 0  # never published by itself


def test_a_new_quarantine_replaces_the_pending_one_of_the_same_source() -> None:
    with Database(":memory:") as db:
        db.quarantine_add("q", 1, "count_drop", [_quarantine_event("Uno")], NOW)
        db.quarantine_add("q", 2, "count_drop", [_quarantine_event("Dos")], NOW)
        db.quarantine_add("other", 2, "count_drop", [_quarantine_event("Tres")], NOW)
        assert [i.event.title for i in db.quarantine_pending("q")] == ["Dos"]
        assert {i.source_id for i in db.quarantine_pending()} == {"q", "other"}


def test_quarantine_status_changes_leave_the_pending_list() -> None:
    with Database(":memory:") as db:
        db.quarantine_add("q", 1, "count_drop", [_quarantine_event("Uno")], NOW)
        assert db.quarantine_close("q", "accepted") == 1
        assert db.quarantine_pending("q") == []
        assert db.quarantine_close("q", "accepted") == 0
