from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from canarias_events.database import Database
from canarias_events.date_range import TZ
from canarias_events.http import HttpError, SafeHttpClient
from canarias_events.models import Event
from canarias_events.pipeline import run_import
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceConfig,
    SourceFetchResult,
)
from tests.conftest import NOW, WINDOW
from tests.test_database import make_cfg


class FakeAdapter(SourceAdapter):
    source_type = "fake"

    def __init__(
        self,
        config: SourceConfig,
        events: list[Event] | None = None,
        error: Exception | None = None,
        discarded: list[tuple[str, str | None]] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        super().__init__(config)
        self.events = events or []
        self.error = error
        self.discarded = discarded or []
        self.warnings = warnings or []
        self.calls = 0

    def fetch(self, ctx: FetchContext) -> SourceFetchResult:
        self.calls += 1
        if self.error:
            raise self.error
        return SourceFetchResult(
            events=list(self.events),
            records_received=len(self.events) + len(self.discarded),
            discarded=list(self.discarded),
            warnings=list(self.warnings),
            pages=2,
        )


def ev(
    source: str,
    event_id: str,
    title: str,
    day: int = 21,
    end_day: int | None = None,
    island: str | None = "Tenerife",
    municipality: str | None = "Adeje",
    hour: int = 20,
    month: int = 10,
) -> Event:
    start = datetime(2026, month, day, hour, 0, tzinfo=TZ)
    end = datetime(2026, month, end_day, hour + 1, 0, tzinfo=TZ) if end_day else None
    return Event(
        title=title,
        start_datetime=start,
        end_datetime=end,
        island=island,
        municipality=municipality,
        source_name=f"Source {source}",
        source_event_id=event_id,
    )


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "p.sqlite")
    yield database
    database.close()


@pytest.fixture
def client() -> Iterator[SafeHttpClient]:
    http = SafeHttpClient(resolver=lambda _h: ["93.184.216.34"], min_interval=0, retries=0)
    yield http
    http.close()


def run(
    db: Database,
    client: SafeHttpClient,
    adapters: dict[str, FakeAdapter],
    configs: list[SourceConfig] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    sources = configs or [a.config for a in adapters.values()]
    factory: Callable[[SourceConfig], SourceAdapter] = lambda cfg: adapters[cfg.id]  # noqa: E731
    return run_import(
        db, sources, WINDOW, client=client, now=NOW, adapter_factory=factory, **kwargs
    )


def fake(source_id: str, *events: Event, **kwargs: Any) -> FakeAdapter:
    return FakeAdapter(make_cfg(source_id), list(events), **kwargs)


# --------------------------------------------------------------------------- isolation


def test_one_failing_source_does_not_stop_the_others(db: Database, client: SafeHttpClient) -> None:
    good = fake("good", ev("good", "1", "Concierto"))
    bad = fake("bad", error=HttpError(503, "HTTP 503 for https://bad.test"))
    report = run(db, client, {"bad": bad, "good": good})

    assert report["sources_attempted"] == ["bad", "good"]
    assert report["sources_succeeded"] == ["good"]
    assert report["sources_failed"] == ["bad"]
    assert report["sources"]["bad"]["status"] == "failed"
    assert "HTTP 503" in report["sources"]["bad"]["error"]
    assert report["sources"]["good"]["new"] == 1
    assert report["errors"] == [{"source": "bad", "error": report["sources"]["bad"]["error"]}]
    assert db.count("events") == 1
    rows = db.source_rows()
    assert rows["bad"]["last_error"] is not None
    assert rows["good"]["last_success_at"] is not None
    assert db.get_run() is not None


def test_report_is_stored_in_import_runs(db: Database, client: SafeHttpClient) -> None:
    report = run(db, client, {"a": fake("a", ev("a", "1", "Concierto"))})
    stored = db.get_run(report["run_id"])
    assert stored is not None
    assert stored.report is not None
    assert stored.report["totals"] == report["totals"]
    assert stored.finished_at is not None
    assert stored.window_start == WINDOW.start.isoformat()


def test_report_window_and_timing_fields(db: Database, client: SafeHttpClient) -> None:
    report = run(db, client, {"a": fake("a", ev("a", "1", "Concierto"))})
    assert report["window"] == {
        "start": WINDOW.start.isoformat(),
        "end": WINDOW.end.isoformat(),
        "start_date": "2026-10-19",
        "end_date_inclusive": "2026-10-25",
    }
    assert report["executed_at"] == NOW.isoformat()
    source = report["sources"]["a"]
    assert source["pages"] == 2
    assert source["duration_s"] >= 0
    assert source["records_received"] == 1


# --------------------------------------------------------------------------- window


def test_central_window_filter_including_multi_day(db: Database, client: SafeHttpClient) -> None:
    events = [
        ev("a", "inside", "Dentro", day=21),
        ev("a", "starts-before", "Empieza antes", day=17, end_day=20),  # spans into the window
        ev("a", "spans-all", "Todo el periodo", day=10, end_day=30),
        ev("a", "before", "Pasado", day=15),
        ev("a", "after", "Futuro", day=27),
        ev("a", "ends-before", "Termina antes", day=17, end_day=18),
        ev("a", "next-midnight", "Lunes siguiente", day=26, hour=0),
    ]
    report = run(db, client, {"a": fake("a", *events)})
    stats = report["sources"]["a"]
    assert stats["valid_events"] == 7
    assert stats["in_window"] == 3
    assert stats["discarded"] == {"outside window": 4}
    titles = {e.title for e in db.all_events()}
    assert titles == {"Dentro", "Empieza antes", "Todo el periodo"}


def test_event_without_any_start_is_discarded(db: Database, client: SafeHttpClient) -> None:
    undated = Event.model_construct(
        **{**ev("a", "x", "Sin fecha").model_dump(), "start_datetime": None, "start_date": None}
    )
    report = run(db, client, {"a": fake("a", undated)})
    assert report["sources"]["a"]["discarded"] == {"missing start date": 1}


def test_all_day_event_uses_inclusive_dates(db: Database, client: SafeHttpClient) -> None:
    fiesta = Event(
        title="Fiesta",
        start_date=date(2026, 10, 25),
        end_date=date(2026, 10, 25),
        all_day=True,
        source_name="Source a",
        source_event_id="f",
    )
    report = run(db, client, {"a": fake("a", fiesta)})
    assert report["sources"]["a"]["in_window"] == 1


def test_adapter_discards_are_counted_by_reason(db: Database, client: SafeHttpClient) -> None:
    adapter = fake(
        "a",
        ev("a", "1", "Ok"),
        discarded=[("invalid title: empty", "x"), ("invalid title: empty", "y"), ("no date", None)],
        warnings=["page 3 skipped"],
    )
    report = run(db, client, {"a": adapter})
    stats = report["sources"]["a"]
    assert stats["records_received"] == 4
    assert stats["discarded"] == {"invalid title: empty": 2, "no date": 1}
    assert stats["warnings"] == ["page 3 skipped"]
    assert report["warnings"] == [{"source": "a", "warning": "page 3 skipped"}]


# --------------------------------------------------------------------------- selection


def test_island_filter_discards_other_islands_and_unknown(
    db: Database, client: SafeHttpClient
) -> None:
    events = [
        ev("a", "1", "Tinerfeño", island="Tenerife"),
        ev("a", "2", "Canario", island="Gran Canaria"),
        ev("a", "3", "Sin isla", island=None, municipality=None),
    ]
    report = run(db, client, {"a": fake("a", *events)}, island="tenerife")
    stats = report["sources"]["a"]
    assert stats["in_window"] == 1
    assert stats["discarded"] == {"island filter": 2}
    assert report["params"]["island"] == "Tenerife"
    assert [e.title for e in db.all_events()] == ["Tinerfeño"]


def test_island_filter_skips_sources_of_other_islands(db: Database, client: SafeHttpClient) -> None:
    lanzarote = FakeAdapter(make_cfg("lz", islands=["Lanzarote"]), [ev("lz", "1", "Timanfaya")])
    multi = FakeAdapter(
        make_cfg("multi", islands=["Tenerife", "La Gomera"]), [ev("multi", "1", "Mixto")]
    )
    report = run(db, client, {"lz": lanzarote, "multi": multi}, island="Tenerife")
    assert lanzarote.calls == 0
    assert report["sources_skipped"] == [{"id": "lz", "reason": "serves other islands"}]
    assert report["sources_succeeded"] == ["multi"]


def test_invalid_island_is_an_error(db: Database, client: SafeHttpClient) -> None:
    with pytest.raises(ValueError, match="unknown island"):
        run(db, client, {"a": fake("a")}, island="Atlantis")


def test_source_filter_and_unknown_source(db: Database, client: SafeHttpClient) -> None:
    a, b = fake("a", ev("a", "1", "Uno")), fake("b", ev("b", "1", "Dos"))
    report = run(db, client, {"a": a, "b": b}, source_ids=["b"])
    assert (a.calls, b.calls) == (0, 1)
    assert report["sources_attempted"] == ["b"]
    with pytest.raises(ValueError, match="unknown source"):
        run(db, client, {"a": a, "b": b}, source_ids=["nope"])


def test_disabled_sources_are_not_run(db: Database, client: SafeHttpClient) -> None:
    off = FakeAdapter(make_cfg("off", enabled=False), [ev("off", "1", "Uno")])
    on = fake("on", ev("on", "1", "Dos"))
    report = run(db, client, {"off": off, "on": on})
    assert off.calls == 0
    assert report["sources_attempted"] == ["on"]
    assert {"id": "off", "reason": "disabled"} in report["sources_skipped"]


def test_missing_api_key_is_skipped_not_failed(db: Database, client: SafeHttpClient) -> None:
    keyed = FakeAdapter(make_cfg("tm", auth_env="TM_KEY"), [ev("tm", "1", "Rock")])
    report = run(db, client, {"tm": keyed}, env={})
    assert keyed.calls == 0
    assert report["sources_skipped"] == [{"id": "tm", "reason": "missing API key (TM_KEY)"}]
    assert report["sources_failed"] == []
    report = run(db, client, {"tm": keyed}, env={"TM_KEY": "k"})
    assert report["sources_succeeded"] == ["tm"]


# --------------------------------------------------------------------------- dedup / idempotency


def test_second_identical_run_adds_and_updates_nothing(
    db: Database, client: SafeHttpClient
) -> None:
    adapters = {"a": fake("a", ev("a", "1", "Uno"), ev("a", "2", "Dos otra cosa", day=22))}
    first = run(db, client, adapters)
    second = run(db, client, adapters)
    assert (first["sources"]["a"]["new"], first["sources"]["a"]["updated"]) == (2, 0)
    stats = second["sources"]["a"]
    assert (stats["new"], stats["updated"], stats["merged"]) == (0, 0, 0)
    assert stats["unchanged"] == 2
    assert db.count("events") == 2
    assert second["totals"]["events_in_window"] == 2


def test_changed_record_counts_as_updated(db: Database, client: SafeHttpClient) -> None:
    run(db, client, {"a": fake("a", ev("a", "1", "Uno"))})
    changed = ev("a", "1", "Uno").model_copy(update={"description": "ahora con texto"})
    report = run(db, client, {"a": fake("a", changed)})
    assert report["sources"]["a"]["updated"] == 1


def test_cross_source_duplicate_is_merged_and_counted_once(
    db: Database, client: SafeHttpClient
) -> None:
    a = fake("a", ev("a", "1", "Concierto de Navidad"))
    b = fake("b", ev("b", "9", "CONCIERTO de navidad"))
    report = run(db, client, {"a": a, "b": b})
    assert report["sources"]["a"]["new"] == 1
    assert report["sources"]["b"]["merged"] == 1
    assert report["sources"]["b"]["new"] == 0
    assert report["totals"]["events_in_window"] == 1
    assert report["totals"]["merged"] == 1


def test_possible_duplicates_are_reported(db: Database, client: SafeHttpClient) -> None:
    a = fake("a", ev("a", "1", "Concierto de Navidad", municipality="Adeje"))
    b = fake("b", ev("b", "9", "Concierto de Navidad", municipality="Arona"))
    report = run(db, client, {"a": a, "b": b})
    assert report["sources"]["b"]["possible_duplicates"] == 1
    assert report["totals"]["possible_duplicates"] == 1
    assert report["totals"]["events_in_window"] == 2


def test_totals_count_distinct_events_in_window_from_the_database(
    db: Database, client: SafeHttpClient
) -> None:
    run(db, client, {"a": fake("a", ev("a", "1", "Uno"))})
    report = run(db, client, {"b": fake("b", ev("b", "1", "Algo muy distinto", day=23))})
    assert report["totals"]["events_in_window"] == 2
    assert report["totals"]["new"] == 1


def test_totals_respect_the_island_filter(db: Database, client: SafeHttpClient) -> None:
    run(db, client, {"a": fake("a", ev("a", "1", "Uno", island="Gran Canaria"))})
    report = run(db, client, {"b": fake("b", ev("b", "1", "Dos otra", day=23))}, island="Tenerife")
    assert report["totals"]["events_in_window"] == 1


def test_all_sources_failing_is_reflected_in_the_report(
    db: Database, client: SafeHttpClient
) -> None:
    report = run(db, client, {"a": fake("a", error=RuntimeError("boom"))})
    assert report["sources_succeeded"] == []
    assert report["sources"]["a"]["error"] == "RuntimeError: boom"
