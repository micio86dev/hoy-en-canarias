from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from canarias_events.crawl_settings import CrawlSettings, SnapshotSettings
from canarias_events.database import Database
from canarias_events.date_range import TZ
from canarias_events.http import HttpError, SafeHttpClient
from canarias_events.models import Event
from canarias_events.pipeline import accept_quarantine, run_import
from canarias_events.snapshots import save_snapshot
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
    adapters: Mapping[str, FakeAdapter],
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


def test_blocked_web_source_is_a_failure_and_ingests_nothing(
    db: Database, client: SafeHttpClient
) -> None:
    class Blocked(FakeAdapter):
        def fetch(self, ctx: FetchContext) -> SourceFetchResult:
            return SourceFetchResult(
                status="blocked",
                extraction_ok=False,
                warnings=["blocked: HTTP 403 for https://x.example.org/ (not bypassed)"],
            )

    cfg = make_cfg("web1")
    report = run_import(
        db, [cfg], WINDOW, client=client, now=NOW, adapter_factory=lambda c: Blocked(c)
    )
    assert report["sources_failed"] == ["web1"]
    stats = report["sources"]["web1"]
    assert stats["status"] == "failed"
    assert "blocked" in stats["error"] and "403" in stats["error"]
    assert stats["strategy"] is None and stats["extraction_ok"] is False
    assert stats["source_status"] == "blocked"


def test_extraction_details_of_tiered_adapters_are_reported(
    db: Database, client: SafeHttpClient
) -> None:
    class Tiered(FakeAdapter):
        def fetch(self, ctx: FetchContext) -> SourceFetchResult:
            result = super().fetch(ctx)
            result.strategy_used = "blocks"
            result.extraction_ok = True
            result.tiers = [{"tier": "jsonld", "candidates": 0, "seconds": 0.01}]
            return result

    cfg = make_cfg("web2")
    events = [ev("web2", "w1", "Concierto")]
    report = run_import(
        db,
        [cfg],
        WINDOW,
        client=client,
        now=NOW,
        adapter_factory=lambda c: Tiered(c, events=events),
    )
    stats = report["sources"]["web2"]
    assert stats["status"] == "succeeded"
    assert (stats["strategy"], stats["extraction_ok"], stats["source_status"]) == (
        "blocks",
        True,
        "ok",
    )
    assert stats["tiers"][0]["tier"] == "jsonld"


def test_plain_adapters_keep_the_old_report_shape(db: Database, client: SafeHttpClient) -> None:
    cfg = make_cfg("plain")
    report = run_import(
        db, [cfg], WINDOW, client=client, now=NOW,
        adapter_factory=lambda c: FakeAdapter(c, events=[ev("plain", "p1", "Normal")]),
    )  # fmt: skip
    assert "strategy" not in report["sources"]["plain"]


def test_ai_settings_reach_the_adapters(db: Database, client: SafeHttpClient) -> None:
    from canarias_events.extract.llm import AiSettings

    seen: list[object] = []

    class Spy(FakeAdapter):
        def fetch(self, ctx: FetchContext) -> SourceFetchResult:
            seen.append(ctx.ai)
            return super().fetch(ctx)

    settings = AiSettings(enabled=True)
    run_import(db, [make_cfg("spy")], WINDOW, client=client, now=NOW,
               adapter_factory=lambda c: Spy(c), ai=settings)  # fmt: skip
    run_import(
        db, [make_cfg("spy")], WINDOW, client=client, now=NOW, adapter_factory=lambda c: Spy(c)
    )
    assert seen == [settings, None]


# --------------------------------------------------------------------------- history and statuses


class Scripted(FakeAdapter):
    """Returns a prepared result (status, hash, warnings) for the status-mapping tests."""

    def __init__(self, config: SourceConfig, result: SourceFetchResult) -> None:
        super().__init__(config)
        self.result = result

    def fetch(self, ctx: FetchContext) -> SourceFetchResult:
        return self.result


def run_scripted(
    db: Database, client: SafeHttpClient, result: SourceFetchResult, source: str = "w"
) -> dict[str, Any]:
    return run_scripted_with(db, client, result, source=source)


def run_scripted_with(
    db: Database,
    client: SafeHttpClient,
    result: SourceFetchResult,
    source: str = "w",
    **kwargs: Any,
) -> dict[str, Any]:
    cfg = make_cfg(source)
    return run_import(
        db,
        [cfg],
        WINDOW,
        client=client,
        now=NOW,
        adapter_factory=lambda c: Scripted(c, result),
        force=True,
        **kwargs,
    )


def test_every_attempted_source_gets_a_history_row(db: Database, client: SafeHttpClient) -> None:
    good = fake("good", ev("good", "1", "Concierto"))
    bad = fake("bad", error=HttpError(503, "HTTP 503 for https://bad.test"))
    report = run(db, client, {"good": good, "bad": bad})

    [row] = db.recent_source_runs("good", limit=5)
    assert (row.run_id, row.status, row.events_found, row.events_in_window) == (
        report["run_id"],
        "succeeded",
        1,
        1,
    )
    assert row.error is None and row.duration_s >= 0
    [failed] = db.recent_source_runs("bad", limit=5)
    assert failed.status == "failed" and "HTTP 503" in (failed.error or "")


def test_history_keeps_strategy_and_listing_hash(db: Database, client: SafeHttpClient) -> None:
    result = SourceFetchResult(
        events=[ev("w", "1", "Concierto")],
        records_received=1,
        strategy_used="blocks",
        extraction_ok=True,
        listing_hash="abc123",
    )
    run_scripted(db, client, result)
    [row] = db.recent_source_runs("w", limit=1)
    assert (row.strategy, row.extraction_ok, row.listing_hash) == ("blocks", True, "abc123")


@pytest.mark.parametrize("status", ["robots_disallowed", "needs_manual_review"])
def test_non_healthy_empty_statuses_are_distinct_from_success(
    db: Database, client: SafeHttpClient, status: str
) -> None:
    result = SourceFetchResult(
        status=status, extraction_ok=False, warnings=[f"{status}: nothing fetched"]
    )
    report = run_scripted(db, client, result)

    assert report["sources"]["w"]["status"] == status
    assert report["sources_succeeded"] == []
    assert report["sources_failed"] == []
    assert report["sources_needs_review"] == ["w"]
    assert report["sources"]["w"]["error"] == f"{status}: nothing fetched"
    assert db.source_rows()["w"]["last_success_at"] is None
    assert db.recent_source_runs("w", limit=1)[0].status == status
    assert report["errors"] == []  # an operator action, not a crash


def test_blocked_without_events_stays_a_failure(db: Database, client: SafeHttpClient) -> None:
    result = SourceFetchResult(status="blocked", extraction_ok=False, warnings=["blocked: 403"])
    report = run_scripted(db, client, result)
    assert report["sources_failed"] == ["w"]
    assert db.recent_source_runs("w", limit=1)[0].source_status == "blocked"


def test_http_forbidden_exception_is_recorded_as_blocked(
    db: Database, client: SafeHttpClient
) -> None:
    run(db, client, {"a": fake("a", error=HttpError(403, "HTTP 403 for https://a.test"))})
    assert db.recent_source_runs("a", limit=1)[0].source_status == "blocked"


# --------------------------------------------------------------------------- quarantine


def seed_history(db: Database, source: str, counts: list[int], strategy: str = "blocks") -> None:
    for n, found in enumerate(counts, start=1):
        db.record_source_run(
            n, source, NOW, status="succeeded", source_status="ok", events_found=found,
            events_in_window=found, duration_s=1.0, error=None, strategy=strategy,
            extraction_ok=True, listing_hash="h", anomalies=[],
        )  # fmt: skip


def web_result(*events: Event, **kw: Any) -> SourceFetchResult:
    fields: dict[str, Any] = {
        "events": list(events),
        "records_received": len(events),
        "strategy_used": "blocks",
        "extraction_ok": True,
        "listing_hash": "h",
    }
    fields.update(kw)
    return SourceFetchResult(**fields)


def test_an_anomalous_run_keeps_the_previous_state_and_quarantines(
    db: Database, client: SafeHttpClient
) -> None:
    run(db, client, {"w": fake("w", ev("w", "1", "Concierto bueno"))})
    seed_history(db, "w", [10, 10, 10])
    before = db.all_events()[0]

    report = run_scripted(db, client, web_result(ev("w", "2", "Basura 1")))

    stats = report["sources"]["w"]
    assert stats["status"] == "anomalous"
    assert stats["anomalies"] == ["count_drop"] and stats["quarantined"] == 1
    assert report["sources_anomalous"] == ["w"]
    assert report["sources_succeeded"] == []
    assert [e.title for e in db.all_events()] == ["Concierto bueno"]  # nothing added, kept
    assert db.all_events() == [before]  # not even touched
    [item] = db.quarantine_pending("w")
    assert item.event.title == "Basura 1" and item.reason == "count_drop"
    assert db.source_rows()["w"]["last_error"].startswith("anomaly: count_drop")
    assert db.recent_source_runs("w", limit=1)[0].anomalies == ["count_drop"]


def test_zero_events_after_history_is_anomalous_without_quarantine(
    db: Database, client: SafeHttpClient
) -> None:
    seed_history(db, "w", [10, 12])
    report = run_scripted(db, client, web_result(strategy_used=None))
    assert report["sources"]["w"]["status"] == "anomalous"
    assert report["sources"]["w"]["anomalies"] == ["empty_after_events"]
    assert db.quarantine_pending() == []


def test_a_normal_run_confirms_and_supersedes_the_quarantine(
    db: Database, client: SafeHttpClient
) -> None:
    seed_history(db, "w", [10, 10, 10])
    run_scripted(db, client, web_result(ev("w", "2", "Basura 1")))
    assert len(db.quarantine_pending("w")) == 1

    ten = [ev("w", str(i), f"Evento {i}", day=20 + i % 4) for i in range(10)]
    report = run_scripted(db, client, web_result(*ten))
    assert report["sources_succeeded"] == ["w"]
    assert db.quarantine_pending("w") == []
    assert db.count("events") == 10


def test_accepting_the_quarantine_publishes_the_events(
    db: Database, client: SafeHttpClient
) -> None:
    seed_history(db, "w", [10, 10, 10])
    run_scripted(db, client, web_result(ev("w", "2", "Concierto nuevo")))
    assert db.count("events") == 0

    assert accept_quarantine(db, "w", now=NOW) == 1
    assert [e.title for e in db.all_events()] == ["Concierto nuevo"]
    assert db.quarantine_pending("w") == []
    assert accept_quarantine(db, "w", now=NOW) == 0


def test_blocked_with_partial_events_is_partial_and_quarantined(
    db: Database, client: SafeHttpClient
) -> None:
    result = web_result(
        ev("w", "1", "Parcial"), status="blocked", warnings=["blocked: HTTP 403 (not bypassed)"]
    )
    report = run_scripted(db, client, result)

    assert report["sources"]["w"]["status"] == "partial"
    assert report["sources_partial"] == ["w"]
    assert report["sources_failed"] == [] and report["sources_succeeded"] == []
    assert report["sources"]["w"]["quarantined"] == 1
    assert db.count("events") == 0
    assert [i.reason for i in db.quarantine_pending("w")] == ["blocked_partial"]
    assert "blocked" in db.source_rows()["w"]["last_error"]
    assert db.source_rows()["w"]["last_success_at"] is None


def test_non_blocking_codes_do_not_quarantine(db: Database, client: SafeHttpClient) -> None:
    seed_history(db, "w", [1, 1, 1], strategy="jsonld")
    report = run_scripted(db, client, web_result(ev("w", "1", "Uno")))
    stats = report["sources"]["w"]
    assert stats["status"] == "succeeded" and stats["anomalies"] == ["strategy_changed"]
    assert db.count("events") == 1


# --------------------------------------------------------------------------- scheduling


def test_a_source_with_a_frequency_is_skipped_after_a_recent_success(
    db: Database, client: SafeHttpClient
) -> None:
    cfg = make_cfg("daily", frequency="daily")
    adapter = FakeAdapter(cfg, [ev("daily", "1", "Concierto")])
    run(db, client, {"daily": adapter}, [cfg])
    report = run(db, client, {"daily": adapter}, [cfg])

    assert adapter.calls == 1
    assert report["sources_attempted"] == []
    [skipped] = report["sources_skipped"]
    assert skipped["id"] == "daily" and "frequency daily" in skipped["reason"]


def test_force_ignores_frequency_and_backoff(db: Database, client: SafeHttpClient) -> None:
    cfg = make_cfg("daily", frequency="daily")
    adapter = FakeAdapter(cfg, [ev("daily", "1", "Concierto")])
    run(db, client, {"daily": adapter}, [cfg])
    report = run(db, client, {"daily": adapter}, [cfg], force=True)
    assert adapter.calls == 2 and report["sources_skipped"] == []


def test_repeated_temporary_failures_back_off(db: Database, client: SafeHttpClient) -> None:
    cfg = make_cfg("flaky")
    adapter = FakeAdapter(cfg, error=HttpError(503, "HTTP 503"))
    for _ in range(3):
        run(db, client, {"flaky": adapter}, [cfg])
    assert adapter.calls == 3

    report = run(db, client, {"flaky": adapter}, [cfg])  # same instant: still backing off
    assert adapter.calls == 3
    assert "backoff" in report["sources_skipped"][0]["reason"]
    assert report["sources_failed"] == []  # skipped, not failed

    run(db, client, {"flaky": adapter}, [cfg], force=True)
    assert adapter.calls == 4


def test_a_blocked_source_is_not_retried_on_the_next_run(
    db: Database, client: SafeHttpClient
) -> None:
    cfg = make_cfg("w")
    blocked = SourceFetchResult(status="blocked", extraction_ok=False, warnings=["blocked: 403"])
    calls: list[int] = []

    def factory(c: SourceConfig) -> SourceAdapter:
        calls.append(1)
        return Scripted(c, blocked)

    for _ in range(3):
        report = run_import(db, [cfg], WINDOW, client=client, now=NOW, adapter_factory=factory)
    assert len(calls) == 1
    assert "cool-down" in report["sources_skipped"][0]["reason"]


# --------------------------------------------------------------------------- concurrency


class Gate(FakeAdapter):
    """Waits for a peer: only finishes when two sources really run at the same time."""

    barrier = threading.Barrier(2, timeout=3)

    def fetch(self, ctx: FetchContext) -> SourceFetchResult:
        Gate.barrier.wait()
        return super().fetch(ctx)


def test_sources_run_concurrently_and_reports_keep_the_configured_order(
    db: Database, client: SafeHttpClient
) -> None:
    Gate.barrier = threading.Barrier(2, timeout=3)
    adapters = {
        "b": Gate(make_cfg("b"), [ev("b", "1", "Uno")]),
        "a": Gate(make_cfg("a"), [ev("a", "1", "Dos otra", day=22)]),
    }
    report = run(db, client, adapters, crawl=CrawlSettings(concurrency=2))
    assert report["sources_succeeded"] == ["b", "a"]
    assert list(report["sources"]) == ["b", "a"]
    assert db.count("events") == 2


def test_concurrency_one_runs_sources_one_at_a_time(db: Database, client: SafeHttpClient) -> None:
    active = [0]
    peak = [0]
    lock = threading.Lock()

    class Probe(FakeAdapter):
        def fetch(self, ctx: FetchContext) -> SourceFetchResult:
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.02)
            with lock:
                active[0] -= 1
            return super().fetch(ctx)

    adapters = {n: Probe(make_cfg(n), [ev(n, "1", f"Evento {n}")]) for n in "abc"}
    run(db, client, adapters, crawl=CrawlSettings(concurrency=1))
    assert peak[0] == 1
    run(db, client, adapters, crawl=CrawlSettings(concurrency=3), force=True)
    assert peak[0] > 1


def test_a_failing_source_does_not_break_concurrent_ones(
    db: Database, client: SafeHttpClient
) -> None:
    adapters = {
        "bad": fake("bad", error=RuntimeError("boom")),
        "good": fake("good", ev("good", "1", "Concierto")),
    }
    report = run(db, client, adapters, crawl=CrawlSettings(concurrency=2))
    assert report["sources_failed"] == ["bad"] and report["sources_succeeded"] == ["good"]


# --------------------------------------------------------------------------- snapshots


def test_failed_and_anomalous_runs_leave_a_snapshot_when_enabled(
    db: Database, client: SafeHttpClient, tmp_path: Path
) -> None:
    crawl = CrawlSettings(snapshots=SnapshotSettings(enabled=True))
    seed_history(db, "w", [10, 10, 10])
    page = web_result(ev("w", "1", "Uno"), snapshot="<html>listing</html>")
    report = run_scripted_with(db, client, page, crawl=crawl, snapshot_dir=tmp_path)

    shot = Path(report["sources"]["w"]["snapshot"])
    assert shot.read_text(encoding="utf-8") == "<html>listing</html>"
    assert tmp_path / "w" in shot.parents


def test_healthy_runs_and_disabled_settings_save_nothing(
    db: Database, client: SafeHttpClient, tmp_path: Path
) -> None:
    snaps = tmp_path / "snaps"
    healthy = web_result(ev("w", "1", "Uno"), snapshot="<html>ok</html>")
    on = CrawlSettings(snapshots=SnapshotSettings(enabled=True))
    report = run_scripted_with(db, client, healthy, crawl=on, snapshot_dir=snaps)
    assert "snapshot" not in report["sources"]["w"]

    bad = web_result(status="needs_manual_review", extraction_ok=False, snapshot="<html/>")
    report = run_scripted_with(db, client, bad, crawl=CrawlSettings(), snapshot_dir=snaps)
    assert "snapshot" not in report["sources"]["w"] and not snaps.exists()
    report = run_scripted_with(db, client, bad, crawl=on, snapshot_dir=None)
    assert "snapshot" not in report["sources"]["w"]


def test_old_snapshots_are_pruned_at_run_start(
    db: Database, client: SafeHttpClient, tmp_path: Path
) -> None:
    from datetime import timedelta

    crawl = CrawlSettings(snapshots=SnapshotSettings(enabled=True, retention_days=5))
    stale = save_snapshot(tmp_path, "gone", "x", NOW - timedelta(days=9), crawl.snapshots)
    assert stale is not None
    run_scripted_with(
        db, client, web_result(ev("w", "1", "Uno")), crawl=crawl, snapshot_dir=tmp_path
    )
    assert not stale.exists()
