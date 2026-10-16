"""SQLite persistence: schema, idempotent upserts with provenance-aware merging, HTTP cache.

Rules enforced here:

* Re-importing the same record changes nothing; "updated" means the source record's content
  hash changed.
* Events missing from a feed are never deleted.
* A merge never overwrites a non-null value with a null one; when two different sources supply
  a field the existing value wins unless the new source has a higher ``priority``. The source
  that supplied each field is recorded in ``field_provenance``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from canarias_events.date_range import TZ
from canarias_events.http import CacheEntry, mask_secrets
from canarias_events.models import Event
from canarias_events.normalize import canonical_url
from canarias_events.sources.base import SourceConfig

DEFAULT_DB_PATH = "data/events.sqlite"
DB_PATH_ENV = "CANARIAS_DB_PATH"
DEFAULT_PRIORITY = 50

_SCHEMA_V1 = """
CREATE TABLE sources (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, adapter TEXT NOT NULL, url TEXT NOT NULL,
    homepage TEXT, license TEXT, enabled INTEGER NOT NULL DEFAULT 1,
    islands TEXT NOT NULL DEFAULT '[]', priority INTEGER NOT NULL DEFAULT 50,
    last_success_at TEXT, last_error TEXT, last_run_id INTEGER
);
CREATE TABLE events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL, description TEXT, summary TEXT, category TEXT, tags TEXT NOT NULL,
    start_datetime TEXT, end_datetime TEXT, start_date TEXT, end_date TEXT,
    timezone TEXT NOT NULL, all_day INTEGER NOT NULL,
    venue_name TEXT, address TEXT, municipality TEXT, island TEXT, postal_code TEXT,
    latitude REAL, longitude REAL,
    price_min REAL, price_max REAL, currency TEXT, is_free INTEGER,
    booking_url TEXT, ticket_url TEXT, official_event_url TEXT, image_url TEXT,
    organizer_name TEXT, organizer_url TEXT, contact_email TEXT,
    source_name TEXT NOT NULL, source_url TEXT, source_event_id TEXT, source_type TEXT,
    source_license TEXT, extra TEXT NOT NULL,
    content_hash TEXT NOT NULL, field_provenance TEXT NOT NULL,
    first_seen_at TEXT, last_seen_at TEXT, imported_at TEXT, last_verified_at TEXT,
    updated_at TEXT
);
CREATE INDEX idx_events_start_date ON events(start_date);
CREATE TABLE event_sources (
    event_id TEXT NOT NULL REFERENCES events(id),
    source_id TEXT NOT NULL REFERENCES sources(id),
    source_event_id TEXT NOT NULL,
    source_url TEXT, official_event_url TEXT, canonical_official_url TEXT,
    raw_data TEXT NOT NULL, content_hash TEXT NOT NULL,
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    field_provenance TEXT NOT NULL,
    match_method TEXT NOT NULL CHECK (match_method IN ('source_id', 'url', 'fuzzy')),
    PRIMARY KEY (source_id, source_event_id)
);
CREATE INDEX idx_event_sources_event ON event_sources(event_id);
CREATE INDEX idx_event_sources_url ON event_sources(canonical_official_url);
CREATE TABLE import_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
    window_start TEXT NOT NULL, window_end TEXT NOT NULL, params TEXT NOT NULL, report TEXT
);
CREATE TABLE possible_duplicates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id_a TEXT NOT NULL REFERENCES events(id),
    event_id_b TEXT NOT NULL REFERENCES events(id),
    score REAL NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE (event_id_a, event_id_b)
);
CREATE TABLE http_cache (
    url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, body BLOB NOT NULL,
    fetched_at TEXT NOT NULL
);
"""
_SCHEMA_V2 = """
CREATE TABLE source_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL, source_id TEXT NOT NULL, started_at TEXT NOT NULL,
    status TEXT NOT NULL, source_status TEXT,
    events_found INTEGER NOT NULL, events_in_window INTEGER NOT NULL,
    duration_s REAL NOT NULL, error TEXT, strategy TEXT, extraction_ok INTEGER,
    listing_hash TEXT, anomalies TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX idx_source_runs_source ON source_runs(source_id, id);
"""
_SCHEMA_V3 = """
CREATE TABLE quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL, run_id INTEGER NOT NULL, created_at TEXT NOT NULL,
    reason TEXT NOT NULL, event TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'superseded'))
);
CREATE INDEX idx_quarantine_source ON quarantine(source_id, status);
"""
_SCHEMA_V4 = """
CREATE TABLE source_candidates (
    id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
    discovered_via TEXT NOT NULL, discovered_at TEXT NOT NULL,
    verification_state TEXT NOT NULL DEFAULT 'candidate'
        CHECK (verification_state IN ('candidate', 'verified', 'rejected')),
    acquisition_mode TEXT, last_validated_at TEXT, last_score INTEGER, report TEXT
);
"""
_MIGRATIONS = (_SCHEMA_V1, _SCHEMA_V2, _SCHEMA_V3, _SCHEMA_V4)

_JSON_FIELDS = frozenset({"tags", "extra"})
_BOOL_FIELDS = frozenset({"all_day", "is_free"})
_EVENT_FIELDS = tuple(name for name in Event.model_fields if name != "raw_data")
# Fields owned by the database (timestamps) or identity: never merged from a source.
_UNMERGED = frozenset(
    {"id", "raw_data", "first_seen_at", "last_seen_at", "imported_at", "last_verified_at"}
)
# Fields that only make sense together are replaced together, decided on the lead field.
_GROUPS: dict[str, tuple[str, ...]] = {
    "start_datetime": ("start_datetime", "end_datetime", "all_day"),
    "latitude": ("latitude", "longitude"),
}
_GROUP_MEMBERS = frozenset(m for members in _GROUPS.values() for m in members)

Outcome = Literal["new", "updated", "unchanged", "merged"]
MatchMethod = Literal["source_id", "url", "fuzzy"]


@dataclass(frozen=True)
class PossibleDuplicate:
    event_id: str
    score: float
    reason: str


@dataclass(frozen=True)
class Decision:
    """Where an incoming event goes: its existing link, another event, or a new event."""

    kind: Literal["existing", "merge", "new"]
    event_id: str | None = None
    method: MatchMethod = "source_id"
    possibles: tuple[PossibleDuplicate, ...] = ()


@dataclass
class UpsertResult:
    event_id: str
    outcome: Outcome
    possible_duplicates: int = 0


@dataclass
class Candidate:
    """An existing event with the ids of the sources that already feed it."""

    event: Event
    source_ids: frozenset[str]


@dataclass
class ImportRun:
    id: int
    started_at: str
    finished_at: str | None
    window_start: str
    window_end: str
    params: dict[str, Any]
    report: dict[str, Any] | None = field(default=None)


@dataclass(frozen=True)
class SourceRun:
    """One source in one import run: the health history that anomaly detection compares to."""

    run_id: int
    source_id: str
    started_at: str
    # succeeded | partial | anomalous | failed | robots_disallowed | needs_manual_review
    status: str
    source_status: str | None  # what the adapter reported: ok | blocked | robots_disallowed | ...
    events_found: int
    events_in_window: int
    duration_s: float
    error: str | None
    strategy: str | None
    extraction_ok: bool | None
    listing_hash: str | None
    anomalies: list[str]


@dataclass(frozen=True)
class QuarantinedEvent:
    """An event from a doubtful run, held back from the events table and the exports."""

    id: int
    source_id: str
    run_id: int
    created_at: str
    reason: str
    event: Event


@dataclass(frozen=True)
class SourceCandidate:
    """A possible source found by discovery or given by a person; never run automatically."""

    id: str
    url: str
    kind: str  # web | feed | api | dataset | social
    discovered_via: str
    discovered_at: str
    verification_state: str  # candidate | verified | rejected
    acquisition_mode: str | None
    last_validated_at: str | None
    last_score: int | None
    report: dict[str, Any] | None


def default_db_path() -> Path:
    return Path(os.environ.get(DB_PATH_ENV) or DEFAULT_DB_PATH)


def source_key(event: Event) -> str:
    """Stable per-source identity: the source's own id, else a hash of the identifying fields."""
    if event.source_event_id:
        return event.source_event_id
    return (
        "h:"
        + uuid.uuid5(
            uuid.NAMESPACE_URL,
            "|".join(
                str(p)
                for p in (event.title, event.start_date, event.venue_name, event.municipality)
            ),
        ).hex
    )


# --------------------------------------------------------------------------- serialization


def _encode(name: str, value: Any) -> Any:
    if name in _JSON_FIELDS:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    return value


def _decode(name: str, value: Any) -> Any:
    if name in _JSON_FIELDS:
        return json.loads(value) if value else ([] if name == "tags" else {})
    if name in _BOOL_FIELDS and value is not None:
        return bool(value)
    return value


def _candidate_row(row: sqlite3.Row) -> SourceCandidate:
    return SourceCandidate(
        id=row["id"], url=row["url"], kind=row["kind"], discovered_via=row["discovered_via"],
        discovered_at=row["discovered_at"], verification_state=row["verification_state"],
        acquisition_mode=row["acquisition_mode"], last_validated_at=row["last_validated_at"],
        last_score=row["last_score"], report=json.loads(row["report"]) if row["report"] else None,
    )  # fmt: skip


def row_to_event(row: sqlite3.Row) -> Event:
    return Event.model_validate({name: _decode(name, row[name]) for name in _EVENT_FIELDS})


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


# --------------------------------------------------------------------------- database


class Database:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # Worker threads only use the HTTP cache (behind its lock); results are stored serially.
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    @classmethod
    def memory_copy(cls, path: Path | str) -> Database:
        """Throwaway in-memory copy of a database file (dry runs): the file is never written."""
        copy = cls(":memory:")
        source = Path(path)
        if source.exists():
            reader = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
            try:
                reader.backup(copy.conn)
            finally:
                reader.close()
            copy._migrate()  # an older file is upgraded in memory only
        return copy

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def schema_version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def _migrate(self) -> None:
        version = self.schema_version
        if version > len(_MIGRATIONS):
            raise RuntimeError(f"database schema v{version} is newer than this program")
        for number in range(version, len(_MIGRATIONS)):
            script = _MIGRATIONS[number]
            self.conn.executescript(
                f"BEGIN;\n{script}\nPRAGMA user_version = {number + 1};\nCOMMIT;"
            )

    # ------------------------------------------------------------------ sources

    def sync_sources(self, configs: list[SourceConfig]) -> None:
        with self.conn:
            for cfg in configs:
                self.conn.execute(
                    """INSERT INTO sources (id, name, adapter, url, homepage, license, enabled,
                                            islands, priority)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET name=excluded.name, adapter=excluded.adapter,
                         url=excluded.url, homepage=excluded.homepage, license=excluded.license,
                         enabled=excluded.enabled, islands=excluded.islands,
                         priority=excluded.priority""",
                    (cfg.id, cfg.name, cfg.adapter, cfg.url, cfg.homepage, cfg.license,
                     int(cfg.enabled), json.dumps(cfg.islands), cfg.priority),
                )  # fmt: skip

    def priority_of(self, source_id: str) -> int:
        row = self.conn.execute(
            "SELECT priority FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        return int(row[0]) if row else DEFAULT_PRIORITY

    def record_source_result(
        self, source_id: str, run_id: int, *, error: str | None, now: datetime
    ) -> None:
        with self.conn:
            if error is None:
                self.conn.execute(
                    "UPDATE sources SET last_success_at=?, last_error=NULL, last_run_id=?"
                    " WHERE id=?",
                    (now.isoformat(), run_id, source_id),
                )
            else:
                self.conn.execute(
                    "UPDATE sources SET last_error=?, last_run_id=? WHERE id=?",
                    (error, run_id, source_id),
                )

    def source_rows(self) -> dict[str, sqlite3.Row]:
        return {r["id"]: r for r in self.conn.execute("SELECT * FROM sources")}

    # ------------------------------------------------------------------ source history

    def record_source_run(
        self, run_id: int, source_id: str, started_at: datetime, *, status: str,
        source_status: str | None, events_found: int, events_in_window: int, duration_s: float,
        error: str | None, strategy: str | None, extraction_ok: bool | None,
        listing_hash: str | None, anomalies: list[str],
    ) -> None:  # fmt: skip
        with self.conn:
            self.conn.execute(
                """INSERT INTO source_runs (run_id, source_id, started_at, status, source_status,
                     events_found, events_in_window, duration_s, error, strategy, extraction_ok,
                     listing_hash, anomalies)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, source_id, started_at.isoformat(), status, source_status, events_found,
                 events_in_window, duration_s, error, strategy,
                 None if extraction_ok is None else int(extraction_ok), listing_hash,
                 json.dumps(anomalies)),
            )  # fmt: skip

    def recent_source_runs(self, source_id: str, limit: int) -> list[SourceRun]:
        """The latest ``limit`` runs of a source, newest first."""
        rows = self.conn.execute(
            "SELECT * FROM source_runs WHERE source_id = ? ORDER BY id DESC LIMIT ?",
            (source_id, limit),
        )
        return [
            SourceRun(
                run_id=r["run_id"], source_id=r["source_id"], started_at=r["started_at"],
                status=r["status"], source_status=r["source_status"],
                events_found=r["events_found"], events_in_window=r["events_in_window"],
                duration_s=r["duration_s"], error=r["error"], strategy=r["strategy"],
                extraction_ok=None if r["extraction_ok"] is None else bool(r["extraction_ok"]),
                listing_hash=r["listing_hash"], anomalies=json.loads(r["anomalies"]),
            )
            for r in rows
        ]  # fmt: skip

    # ------------------------------------------------------------------ quarantine

    def quarantine_add(
        self, source_id: str, run_id: int, reason: str, events: list[Event], now: datetime,
        *, replace: bool = True,
    ) -> int:  # fmt: skip
        """Hold ``events`` back; by default they replace what was pending for the source."""
        if not events:
            return 0  # nothing new to hold: keep what a person may still want to accept
        with self.conn:
            if replace:
                self.conn.execute(
                    "UPDATE quarantine SET status='superseded'"
                    " WHERE source_id=? AND status='pending'",
                    (source_id,),
                )
            self.conn.executemany(
                "INSERT INTO quarantine (source_id, run_id, created_at, reason, event)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        source_id, run_id, now.isoformat(), reason,
                        json.dumps(e.model_dump(mode="json"), ensure_ascii=False, default=str),
                    )
                    for e in events
                ],
            )  # fmt: skip
        return len(events)

    def quarantine_pending(self, source_id: str | None = None) -> list[QuarantinedEvent]:
        query = "SELECT * FROM quarantine WHERE status = 'pending'"
        args: tuple[str, ...] = ()
        if source_id is not None:
            query, args = query + " AND source_id = ?", (source_id,)
        rows = self.conn.execute(query + " ORDER BY source_id, id", args)
        return [
            QuarantinedEvent(
                id=r["id"], source_id=r["source_id"], run_id=r["run_id"],
                created_at=r["created_at"], reason=r["reason"],
                event=Event.model_validate(json.loads(r["event"])),
            )
            for r in rows
        ]  # fmt: skip

    def quarantine_close(self, source_id: str, status: str) -> int:
        """Leave the pending list as ``accepted`` (published) or ``superseded`` (confirmed)."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE quarantine SET status=? WHERE source_id=? AND status='pending'",
                (status, source_id),
            )
        return cur.rowcount

    # ------------------------------------------------------------------ source candidates

    def add_candidate(
        self, url: str, kind: str, via: str, now: datetime, mode: str | None = None
    ) -> bool:
        """Remember a possible source; True when new. An existing one is never touched."""
        from canarias_events.candidates import candidate_id  # avoid an import cycle

        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO source_candidates (id, url, kind, discovered_via,"
                " discovered_at, acquisition_mode) VALUES (?, ?, ?, ?, ?, ?)",
                (candidate_id(url), url, kind, via, now.isoformat(), mode),
            )
        return cur.rowcount == 1

    def get_candidate(self, id_or_url: str) -> SourceCandidate | None:
        row = self.conn.execute(
            "SELECT * FROM source_candidates WHERE id = ? OR url = ?", (id_or_url, id_or_url)
        ).fetchone()
        return _candidate_row(row) if row else None

    def list_candidates(self) -> list[SourceCandidate]:
        rows = self.conn.execute("SELECT * FROM source_candidates ORDER BY discovered_at, id")
        return [_candidate_row(r) for r in rows]

    def update_candidate(
        self, candidate_id: str, *, state: str, mode: str | None, score: int,
        report: dict[str, Any], now: datetime,
    ) -> None:  # fmt: skip
        with self.conn:
            self.conn.execute(
                "UPDATE source_candidates SET verification_state=?, acquisition_mode=?,"
                " last_score=?, report=?, last_validated_at=? WHERE id=?",
                (state, mode, score, json.dumps(report, ensure_ascii=False, default=str),
                 now.isoformat(), candidate_id),
            )  # fmt: skip

    def touch_candidate(self, candidate_id: str, *, report: dict[str, Any], now: datetime) -> None:
        """Record a check that reached no verdict: only when and why, never state or score."""
        with self.conn:
            self.conn.execute(
                "UPDATE source_candidates SET report=?, last_validated_at=? WHERE id=?",
                (json.dumps(report, ensure_ascii=False, default=str), now.isoformat(),
                 candidate_id),
            )  # fmt: skip

    # ------------------------------------------------------------------ import runs

    def start_run(
        self, started_at: datetime, window_start: datetime, window_end: datetime,
        params: dict[str, Any],
    ) -> int:  # fmt: skip
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO import_runs (started_at, window_start, window_end, params)"
                " VALUES (?, ?, ?, ?)",
                (started_at.isoformat(), window_start.isoformat(), window_end.isoformat(),
                 json.dumps(params, default=str)),
            )  # fmt: skip
        return int(cur.lastrowid or 0)

    def finish_run(self, run_id: int, finished_at: datetime, report: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE import_runs SET finished_at=?, report=? WHERE id=?",
                (
                    finished_at.isoformat(),
                    json.dumps(report, ensure_ascii=False, default=str),
                    run_id,
                ),
            )

    def get_run(self, run_id: int | None = None) -> ImportRun | None:
        if run_id is None:
            row = self.conn.execute("SELECT * FROM import_runs ORDER BY id DESC LIMIT 1").fetchone()
        else:
            row = self.conn.execute("SELECT * FROM import_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return ImportRun(
            id=row["id"], started_at=row["started_at"], finished_at=row["finished_at"],
            window_start=row["window_start"], window_end=row["window_end"],
            params=json.loads(row["params"]),
            report=json.loads(row["report"]) if row["report"] else None,
        )  # fmt: skip

    # ------------------------------------------------------------------ lookups

    def find_link(self, source_id: str, source_event_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.conn.execute(
            "SELECT * FROM event_sources WHERE source_id = ? AND source_event_id = ?",
            (source_id, source_event_id),
        ).fetchone()
        return row

    def candidates_by_url(self, official_url: str) -> list[Candidate]:
        canonical = canonical_url(official_url)
        if not canonical:
            return []
        ids = [
            r[0]
            for r in self.conn.execute(
                "SELECT DISTINCT event_id FROM event_sources WHERE canonical_official_url = ?",
                (canonical,),
            )
        ]
        return [self._candidate(i) for i in ids]

    def candidates_on(self, start_date: date) -> list[Candidate]:
        rows = self.conn.execute(
            "SELECT id FROM events WHERE start_date = ?", (start_date.isoformat(),)
        ).fetchall()
        return [self._candidate(r[0]) for r in rows]

    def _candidate(self, event_id: str) -> Candidate:
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        sources = frozenset(
            r[0]
            for r in self.conn.execute(
                "SELECT source_id FROM event_sources WHERE event_id = ?", (event_id,)
            )
        )
        return Candidate(row_to_event(row), sources)

    def get_event(self, event_id: str) -> Event | None:
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return row_to_event(row) if row else None

    def all_events(self) -> list[Event]:
        rows = self.conn.execute("SELECT * FROM events ORDER BY start_date, title, id")
        return [row_to_event(r) for r in rows]

    def event_sources(self, event_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT es.*, s.name AS source_name FROM event_sources es"
            " JOIN sources s ON s.id = es.source_id WHERE es.event_id = ?"
            " ORDER BY es.first_seen_at, es.source_id, es.source_event_id",
            (event_id,),
        ).fetchall()

    def shared_url_groups(self) -> list[tuple[str, str, list[str]]]:
        """``(source_id, canonical_url, event_ids)`` where a source links one URL to 2+ events."""
        rows = self.conn.execute(
            "SELECT DISTINCT source_id, canonical_official_url, event_id FROM event_sources"
            " WHERE canonical_official_url IS NOT NULL"
            " ORDER BY source_id, canonical_official_url, event_id"
        ).fetchall()
        groups: dict[tuple[str, str], list[str]] = {}
        for source_id, url, event_id in rows:
            groups.setdefault((source_id, url), []).append(event_id)
        return [(sid, url, ids) for (sid, url), ids in groups.items() if len(ids) > 1]

    def link_counts(self, event_id: str) -> tuple[int, int]:
        """``(links, distinct sources)`` of an event."""
        row = self.conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT source_id) FROM event_sources WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return int(row[0]), int(row[1])

    def count(self, table: str) -> int:
        if table not in {"events", "event_sources", "possible_duplicates", "sources"}:
            raise ValueError(table)
        return int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608

    def possible_duplicates(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM possible_duplicates ORDER BY id").fetchall()

    # ------------------------------------------------------------------ upsert

    def upsert(
        self, event: Event, source_id: str, decision: Decision, now: datetime
    ) -> UpsertResult:
        key = source_key(event)
        new_hash = event.content_hash()
        with self.conn:
            if decision.kind == "existing":
                return self._update_existing(event, source_id, key, new_hash, now)
            if decision.kind == "merge" and decision.event_id is not None:
                event_id = decision.event_id
                self._merge_into(event_id, event, source_id, now)
                self._insert_link(event_id, event, source_id, key, new_hash, decision.method, now)
                return UpsertResult(event_id, "merged")
            event_id = self._insert_event(event, source_id, now)
            self._insert_link(event_id, event, source_id, key, new_hash, "source_id", now)
            recorded = sum(self._record_possible(event_id, p, now) for p in decision.possibles)
            return UpsertResult(event_id, "new", recorded)

    def _update_existing(
        self, event: Event, source_id: str, key: str, new_hash: str, now: datetime
    ) -> UpsertResult:
        link = self.find_link(source_id, key)
        if link is None:
            raise LookupError(f"no link for {source_id}/{key}")
        event_id: str = link["event_id"]
        if link["content_hash"] == new_hash:
            self.conn.execute(
                "UPDATE event_sources SET last_seen_at=? WHERE source_id=? AND source_event_id=?",
                (now.isoformat(), source_id, key),
            )
            self.conn.execute(
                "UPDATE events SET last_seen_at=?, last_verified_at=? WHERE id=?",
                (now.isoformat(), now.isoformat(), event_id),
            )
            return UpsertResult(event_id, "unchanged")
        self._merge_into(event_id, event, source_id, now)
        self.conn.execute(
            """UPDATE event_sources SET source_url=?, official_event_url=?,
                 canonical_official_url=?, raw_data=?, content_hash=?, last_seen_at=?
               WHERE source_id=? AND source_event_id=?""",
            (event.source_url, event.official_event_url, self._canon(event), self._raw(event),
             new_hash, now.isoformat(), source_id, key),
        )  # fmt: skip
        return UpsertResult(event_id, "updated")

    @staticmethod
    def _canon(event: Event) -> str | None:
        return canonical_url(event.official_event_url) if event.official_event_url else None

    @staticmethod
    def _raw(event: Event) -> str:
        return json.dumps(event.raw_data, ensure_ascii=False, default=str)

    def _insert_event(self, event: Event, source_id: str, now: datetime) -> str:
        event_id = uuid.uuid4().hex
        stamp = now.isoformat()
        provenance = {
            name: source_id for name in _EVENT_FIELDS
            if name not in _UNMERGED and not _is_empty(getattr(event, name))
        }  # fmt: skip
        stored = event.model_copy(
            update={"id": event_id, "first_seen_at": now, "last_seen_at": now,
                    "imported_at": now, "last_verified_at": now}
        )  # fmt: skip
        values = {name: _encode(name, getattr(stored, name)) for name in _EVENT_FIELDS}
        values.update(
            content_hash=stored.content_hash(),
            field_provenance=json.dumps(provenance, sort_keys=True),
            updated_at=stamp,
        )
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        self.conn.execute(
            f"INSERT INTO events ({columns}) VALUES ({marks})", tuple(values.values())
        )  # noqa: S608
        return event_id

    def _insert_link(
        self, event_id: str, event: Event, source_id: str, key: str, content_hash: str,
        method: MatchMethod, now: datetime,
    ) -> None:  # fmt: skip
        stamp = now.isoformat()
        provenance = self.conn.execute(
            "SELECT field_provenance FROM events WHERE id = ?", (event_id,)
        ).fetchone()[0]
        supplied = sorted(k for k, v in json.loads(provenance).items() if v == source_id)
        self.conn.execute(
            """INSERT INTO event_sources (event_id, source_id, source_event_id, source_url,
                 official_event_url, canonical_official_url, raw_data, content_hash,
                 first_seen_at, last_seen_at, field_provenance, match_method)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, source_id, key, event.source_url, event.official_event_url,
             self._canon(event), self._raw(event), content_hash, stamp, stamp,
             json.dumps(supplied), method),
        )  # fmt: skip

    def _record_possible(self, new_id: str, dup: PossibleDuplicate, now: datetime) -> int:
        a, b = sorted((new_id, dup.event_id))
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO possible_duplicates (event_id_a, event_id_b, score, reason,"
            " created_at) VALUES (?, ?, ?, ?, ?)",
            (a, b, dup.score, dup.reason, now.isoformat()),
        )
        return cur.rowcount

    def _merge_into(
        self,
        event_id: str,
        incoming: Event,
        source_id: str,
        now: datetime,
        *,
        fill_only: bool = False,
    ) -> None:
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        current = row_to_event(row)
        provenance: dict[str, str] = json.loads(row["field_provenance"])
        merged, provenance = self._merged(
            current, provenance, incoming, source_id, fill_only=fill_only
        )
        merged = merged.model_copy(update={"last_seen_at": now, "last_verified_at": now})
        new_hash = merged.content_hash()
        changed = new_hash != row["content_hash"]
        values = {
            name: _encode(name, getattr(merged, name))
            for name in _EVENT_FIELDS
            if name not in {"id", "first_seen_at", "imported_at"}
        }
        values.update(
            content_hash=new_hash, field_provenance=json.dumps(provenance, sort_keys=True)
        )
        if changed:
            values["updated_at"] = now.isoformat()
        assignments = ", ".join(f"{name} = ?" for name in values)
        self.conn.execute(
            f"UPDATE events SET {assignments} WHERE id = ?",  # noqa: S608
            (*values.values(), event_id),
        )
        self._refresh_link_provenance(event_id, provenance)

    def absorb_event(self, keep_id: str, drop_id: str, source_id: str, now: datetime) -> None:
        """Fold a stored copy into another event of the same source, in one transaction."""
        dropped = self.get_event(drop_id)
        kept = self.get_event(keep_id)
        if dropped is None or kept is None:
            raise LookupError(f"no event {drop_id if dropped is None else keep_id}")
        with self.conn:
            self._merge_into(keep_id, dropped, source_id, now, fill_only=True)
            # Nothing was fetched: keep the latest real sighting of either copy, not ``now``.
            seen = [e.last_seen_at for e in (kept, dropped) if e.last_seen_at is not None]
            verified = [e.last_verified_at for e in (kept, dropped) if e.last_verified_at]
            self.conn.execute(
                "UPDATE events SET last_seen_at = ?, last_verified_at = ? WHERE id = ?",
                (
                    max(seen).isoformat() if seen else None,
                    max(verified).isoformat() if verified else None,
                    keep_id,
                ),
            )
            self.conn.execute(
                "UPDATE event_sources SET event_id = ?, match_method = 'url' WHERE event_id = ?",
                (keep_id, drop_id),
            )
            provenance: dict[str, str] = json.loads(
                self.conn.execute(
                    "SELECT field_provenance FROM events WHERE id = ?", (keep_id,)
                ).fetchone()[0]
            )
            self._refresh_link_provenance(keep_id, provenance)
            self.conn.execute(
                "DELETE FROM possible_duplicates WHERE event_id_a = ? OR event_id_b = ?",
                (drop_id, drop_id),
            )
            self.conn.execute("DELETE FROM events WHERE id = ?", (drop_id,))

    def _refresh_link_provenance(self, event_id: str, provenance: dict[str, str]) -> None:
        """Each link lists the fields it currently supplies to the event."""
        for (link_source,) in self.conn.execute(
            "SELECT DISTINCT source_id FROM event_sources WHERE event_id = ?", (event_id,)
        ).fetchall():
            supplied = sorted(k for k, v in provenance.items() if v == link_source)
            self.conn.execute(
                "UPDATE event_sources SET field_provenance = ?"
                " WHERE event_id = ? AND source_id = ?",
                (json.dumps(supplied), event_id, link_source),
            )

    def _merged(
        self,
        current: Event,
        provenance: dict[str, str],
        incoming: Event,
        source_id: str,
        *,
        fill_only: bool = False,
    ) -> tuple[Event, dict[str, str]]:
        """Field-wise merge: never null over non-null; priority decides real conflicts.

        With ``fill_only`` (a stored copy folded into its twin) the current values always win.
        """
        incoming_rank = self.priority_of(source_id)
        updates: dict[str, Any] = {}
        result = dict(provenance)

        def takes_over(lead: str) -> bool:
            if fill_only:
                return False
            owner = provenance.get(lead)
            if owner is None or owner == source_id:
                return True
            return incoming_rank > self.priority_of(owner)

        for name in _EVENT_FIELDS:
            if name in _UNMERGED or name in {"tags", "extra"}:
                continue
            if name in _GROUP_MEMBERS and name not in _GROUPS:
                continue
            members = _GROUPS.get(name, (name,))
            new_value = getattr(incoming, name)
            old_value = getattr(current, name)
            if _is_empty(new_value) or new_value == old_value:
                continue
            if any(
                _is_empty(getattr(incoming, member)) for member in members if member != name
            ) and (name == "latitude"):
                continue  # half a coordinate pair never replaces a complete one
            if _is_empty(old_value) or takes_over(name):
                for member in members:
                    updates[member] = getattr(incoming, member)
                    result[member] = source_id
        # `extra` has no per-key provenance: the rank of the source that supplied `source_name`
        # (the record's primary source) decides conflicts.
        union = list(dict.fromkeys([*current.tags, *incoming.tags]))
        extra = (
            {**current.extra, **incoming.extra}
            if not fill_only
            and incoming_rank >= self.priority_of(provenance.get("source_name", source_id))
            else {**incoming.extra, **current.extra}
        )
        updates.update(tags=union, extra=extra)
        return current.model_copy(update=updates), result


# --------------------------------------------------------------------------- HTTP cache


class SqliteHttpCache:
    """``HttpCache`` implementation persisting conditional-request validators in SQLite."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._lock = threading.Lock()  # sources fetch in threads; one sqlite connection

    def get(self, url: str) -> CacheEntry | None:
        with self._lock:
            row = self._db.conn.execute(
                "SELECT etag, last_modified, body FROM http_cache WHERE url = ?",
                (mask_secrets(url),),
            ).fetchone()
        return (row["etag"], row["last_modified"], bytes(row["body"])) if row else None

    def set(self, url: str, etag: str | None, last_modified: str | None, body: bytes) -> None:
        with self._lock, self._db.conn:
            self._db.conn.execute(
                "INSERT INTO http_cache (url, etag, last_modified, body, fetched_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(url) DO UPDATE SET etag=excluded.etag,"
                " last_modified=excluded.last_modified, body=excluded.body,"
                " fetched_at=excluded.fetched_at",
                (  # the key never stores credentials passed as query parameters
                    mask_secrets(url),
                    etag,
                    last_modified,
                    body,
                    datetime.now(TZ).isoformat(),
                ),
            )
