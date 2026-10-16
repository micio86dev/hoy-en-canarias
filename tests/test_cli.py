from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import yaml
from typer.testing import CliRunner

from canarias_events import cli
from canarias_events.cli import app
from canarias_events.database import Database
from canarias_events.date_range import TZ, next_week
from canarias_events.http import SafeHttpClient
from canarias_events.models import Event
from canarias_events.pipeline import run_import
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceConfig,
    SourceFetchResult,
)
from canarias_events.sources.ical import IcalAdapter
from canarias_events.sources.registry import ADAPTERS
from tests.conftest import NOW

runner = CliRunner()


class ParamAdapter(SourceAdapter):
    """Test adapter driven by ``params``: titles, day, island, fail."""

    source_type = "fake"

    def fetch(self, ctx: FetchContext) -> SourceFetchResult:
        params = self.config.params
        if params.get("fail"):
            raise RuntimeError("source exploded")
        result = SourceFetchResult(records_received=len(params.get("titles", [])), pages=1)
        for index, title in enumerate(params.get("titles", [])):
            result.events.append(
                Event(
                    title=title,
                    start_datetime=datetime(2026, 10, params.get("day", 21), 20, 0, tzinfo=TZ),
                    island=params.get("island", "Tenerife"),
                    municipality="Adeje",
                    source_name=self.config.name,
                    source_event_id=f"{self.config.id}-{index}",
                )
            )
        return result


@pytest.fixture(autouse=True)
def fake_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(ADAPTERS, "fake", ParamAdapter)


def write_config(tmp_path: Path, sources: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump({"sources": sources, **extra}), encoding="utf-8")
    return path


def fake_source(source_id: str, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": source_id,
        "name": f"Source {source_id}",
        "adapter": "fake",
        "url": f"https://{source_id}.test/feed",
        "params": {"titles": [f"Evento {source_id}"]},
    }
    data.update(overrides)
    return data


def invoke(*args: str) -> Any:
    return runner.invoke(app, list(args))


def import_args(tmp_path: Path, config: Path, *extra: str) -> list[str]:
    return [
        "import-events", "--config", str(config), "--db", str(tmp_path / "db.sqlite"),
        "--output-dir", str(tmp_path / "out"), *extra,
    ]  # fmt: skip


# --------------------------------------------------------------------------- import-events


def test_import_events_with_explicit_dates(tmp_path: Path) -> None:
    config = write_config(
        tmp_path, [fake_source("a"), fake_source("b", params={"titles": ["Otro"]})]
    )
    result = invoke(*import_args(tmp_path, config, "--from", "2026-10-19", "--to", "2026-10-25"))

    assert result.exit_code == 0, result.output
    assert "2026-10-19" in result.output
    assert "2026-10-25" in result.output
    assert any(line.startswith("a ") and "succeeded" in line for line in result.output.splitlines())
    out = tmp_path / "out"
    events = json.loads((out / "events.json").read_text(encoding="utf-8"))
    assert {e["title"] for e in events} == {"Evento a", "Otro"}
    assert (out / "events.csv").exists()
    report = json.loads((out / "import-report.json").read_text(encoding="utf-8"))
    assert report["window"]["start_date"] == "2026-10-19"
    assert report["sources_succeeded"] == ["a", "b"]
    assert (out / "sources-report.json").exists()


def test_default_window_is_next_week_and_printed(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a")])
    result = invoke(*import_args(tmp_path, config))
    window = next_week()
    assert f"{window.start_date.isoformat()}" in result.output
    assert f"{window.end_date_inclusive.isoformat()}" in result.output
    assert result.exit_code == 0
    report = json.loads((tmp_path / "out" / "import-report.json").read_text(encoding="utf-8"))
    assert report["window"]["start_date"] == window.start_date.isoformat()


@pytest.mark.parametrize(
    "args",
    [
        ["--from", "2026-10-19"],
        ["--to", "2026-10-25"],
        ["--from", "2026-13-01", "--to", "2026-10-25"],
        ["--from", "19/10/2026", "--to", "25/10/2026"],
        ["--from", "2026-10-25", "--to", "2026-10-19"],
    ],
)
def test_invalid_date_options_are_rejected(tmp_path: Path, args: list[str]) -> None:
    config = write_config(tmp_path, [fake_source("a")])
    result = invoke(*import_args(tmp_path, config, *args))
    assert result.exit_code == 2
    assert not (tmp_path / "out").exists()


def test_exit_code_is_zero_if_one_source_succeeds(tmp_path: Path) -> None:
    config = write_config(
        tmp_path, [fake_source("good"), fake_source("bad", params={"fail": True})]
    )
    result = invoke(*import_args(tmp_path, config, "--from", "2026-10-19", "--to", "2026-10-25"))
    assert result.exit_code == 0
    assert "source exploded" in result.output
    assert "failed" in result.output


def test_exit_code_is_zero_when_no_source_is_due(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a", frequency="daily")])
    args = import_args(tmp_path, config, "--from", "2026-10-19", "--to", "2026-10-25")
    assert invoke(*args).exit_code == 0
    again = invoke(*args)
    assert again.exit_code == 0, again.output
    assert "No source due; 1 skipped" in again.output
    assert "No source succeeded" not in again.output


def test_exit_code_is_zero_when_only_some_sources_are_due(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a", frequency="daily"), fake_source("b")])
    args = import_args(tmp_path, config, "--from", "2026-10-19", "--to", "2026-10-25")
    assert invoke(*args).exit_code == 0
    assert invoke(*args).exit_code == 0  # b still runs and succeeds; a is deferred


def test_exit_code_is_one_when_nothing_was_attempted_for_other_reasons(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("off", enabled=False)])
    result = invoke(*import_args(tmp_path, config, "--from", "2026-10-19", "--to", "2026-10-25"))
    assert result.exit_code == 1
    assert "No source succeeded" in result.output


def test_failed_attempts_still_exit_one_next_to_deferred_sources(tmp_path: Path) -> None:
    config = write_config(
        tmp_path,
        [fake_source("a", frequency="daily"), fake_source("bad", params={"fail": True})],
    )
    args = import_args(tmp_path, config, "--from", "2026-10-19", "--to", "2026-10-25")
    assert invoke(*args).exit_code == 0  # a succeeds
    assert invoke(*args).exit_code == 1  # a is deferred, bad is attempted and fails


def test_exit_code_is_one_when_every_source_fails(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("bad", params={"fail": True})])
    result = invoke(*import_args(tmp_path, config, "--from", "2026-10-19", "--to", "2026-10-25"))
    assert result.exit_code == 1
    assert (tmp_path / "out" / "import-report.json").exists()  # the report is still written


def test_source_and_island_filters(tmp_path: Path) -> None:
    config = write_config(
        tmp_path,
        [
            fake_source("a", islands=["Tenerife"]),
            fake_source(
                "b", islands=["La Palma"], params={"titles": ["Palma"], "island": "La Palma"}
            ),
        ],
    )
    base = ("--from", "2026-10-19", "--to", "2026-10-25")
    result = invoke(*import_args(tmp_path, config, *base, "--island", "La Palma"))
    assert result.exit_code == 0, result.output
    events = json.loads((tmp_path / "out" / "events.json").read_text(encoding="utf-8"))
    assert [e["title"] for e in events] == ["Palma"]

    result = invoke(*import_args(tmp_path, config, *base, "--source", "a", "--source", "b"))
    assert result.exit_code == 0


def test_unknown_source_or_island_is_a_usage_error(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a")])
    base = ("--from", "2026-10-19", "--to", "2026-10-25")
    assert invoke(*import_args(tmp_path, config, *base, "--source", "zzz")).exit_code == 2
    assert invoke(*import_args(tmp_path, config, *base, "--island", "Atlantis")).exit_code == 2


def test_missing_config_is_reported(tmp_path: Path) -> None:
    result = invoke(*import_args(tmp_path, tmp_path / "nope.yaml"))
    assert result.exit_code == 2


def test_db_path_defaults_to_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = write_config(tmp_path, [fake_source("a")])
    monkeypatch.setenv("CANARIAS_DB_PATH", str(tmp_path / "env.sqlite"))
    result = invoke(
        "import-events", "--config", str(config), "--output-dir", str(tmp_path / "o"),
        "--from", "2026-10-19", "--to", "2026-10-25",
    )  # fmt: skip
    assert result.exit_code == 0
    assert (tmp_path / "env.sqlite").exists()


def test_second_run_reports_nothing_new(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a")])
    args = import_args(tmp_path, config, "--from", "2026-10-19", "--to", "2026-10-25")
    invoke(*args)
    invoke(*args)
    report = json.loads((tmp_path / "out" / "import-report.json").read_text(encoding="utf-8"))
    assert report["totals"]["new"] == 0
    assert report["totals"]["unchanged"] == 1


# --------------------------------------------------------------------------- list-sources / report


def test_list_sources_table(tmp_path: Path) -> None:
    config = write_config(
        tmp_path,
        [
            fake_source("on", islands=["Tenerife"], license="CC-BY"),
            fake_source("off", enabled=False),
            fake_source("old", enabled=False, stale=True),
            fake_source("tm", auth_env="CLI_TEST_KEY_UNSET"),
        ],
    )
    result = invoke("list-sources", "--config", str(config))
    assert result.exit_code == 0
    lines = {line.split()[0]: line for line in result.output.splitlines() if line.strip()}
    assert "integrated" in lines["on"]
    assert "Tenerife" in lines["on"]
    assert "CC-BY" in lines["on"]
    assert "disabled" in lines["off"]
    assert "stale" in lines["old"]
    assert "skipped" in lines["tm"]
    assert "adapter" in result.output.splitlines()[0]


def test_list_sources_shipped_config() -> None:
    result = invoke("list-sources", "--config", "config/sources.yaml")
    assert result.exit_code == 0
    assert "holaislascanarias" in result.output


def social_args(tmp_path: Path, rows: list[dict[str, Any]] | None = None) -> list[str]:
    config = write_config(tmp_path, [fake_source("a")])
    args = ["list-sources", "--social", "--config", str(config)]
    if rows is not None:
        social = tmp_path / "social.yaml"
        social.write_text(yaml.safe_dump({"sources": rows}), encoding="utf-8")
        args += ["--social-config", str(social)]
    return args


def test_list_sources_social_prints_the_shipped_registry(tmp_path: Path) -> None:
    result = invoke(*social_args(tmp_path, None), "--social-config", "config/social_sources.yaml")
    assert result.exit_code == 0, result.output
    lines = {line.split()[0]: line for line in result.output.splitlines() if line.strip()}
    assert "requires_authorization" in lines["facebook_pages"]
    assert "excluded" in lines["facebook_groups"]
    assert "import_url" in lines["eventbrite_pages"]
    assert "candidate" in lines["radio_insular_feed"]
    assert not any(line.rstrip().endswith("yes") for line in lines.values())  # nothing scheduled
    assert "adapter" not in result.output.splitlines()[0]  # the social table, not the sources one


def test_list_sources_social_marks_what_is_scheduled(tmp_path: Path) -> None:
    rows: list[dict[str, Any]] = [
        {"id": "ok", "platform": "mastodon", "name": "Ok", "adapter": "mastodon",
         "handle": "a@m.example", "acquisition_mode": "rss", "verification_state": "verified",
         "enabled": True, "frequency": "daily"},
        {"id": "fb", "platform": "facebook", "name": "Fb", "kind": "platform",
         "acquisition_mode": "requires_authorization"},
    ]  # fmt: skip
    result = invoke(*social_args(tmp_path, rows))
    lines = {line.split()[0]: line for line in result.output.splitlines() if line.strip()}
    assert lines["ok"].rstrip().endswith("yes")
    assert "no (platform description)" in lines["fb"]


def test_list_sources_social_without_a_file_says_so(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a")])
    result = invoke("list-sources", "--social", "--config", str(config))
    assert result.exit_code == 0
    assert "No social sources" in result.output


def test_report_prints_latest_run(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a"), fake_source("bad", params={"fail": True})])
    db = str(tmp_path / "db.sqlite")
    invoke(*import_args(tmp_path, config, "--from", "2026-10-19", "--to", "2026-10-25"))
    latest = invoke("report", "--db", db)
    assert latest.exit_code == 0, latest.output
    assert "Run 1" in latest.output
    assert "2026-10-19" in latest.output
    assert "source exploded" in latest.output
    assert invoke("report", "--db", db, "--run-id", "1").exit_code == 0
    assert invoke("report", "--db", db, "--run-id", "99").exit_code == 1


def test_report_health_lists_each_source(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a"), fake_source("bad", params={"fail": True})])
    db = str(tmp_path / "db.sqlite")
    invoke(*import_args(tmp_path, config, "--from", "2026-10-19", "--to", "2026-10-25"))
    result = invoke("report", "--db", db, "--health")
    assert result.exit_code == 0, result.output
    lines = {line.split()[0]: line for line in result.output.splitlines()[1:] if line.strip()}
    assert "succeeded" in lines["a"]
    assert "failed" in lines["bad"] and "100%" in lines["bad"]


def test_report_health_uses_history_runs_from_the_config(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a")], crawl={"history_runs": 2})
    for _ in range(3):
        invoke(*import_args(tmp_path, config, "--from", "2026-10-19", "--to", "2026-10-25"))
    db = str(tmp_path / "db.sqlite")
    with Database(db) as check:
        assert len(check.recent_source_runs("a", 50)) == 3
    result = invoke("report", "--db", db, "--health", "--config", str(config))
    assert result.exit_code == 0, result.output
    row = next(line for line in result.output.splitlines() if line.startswith("a "))
    assert row.split()[1] == "2"  # runs column, capped by crawl.history_runs


def test_report_without_runs(tmp_path: Path) -> None:
    result = invoke("report", "--db", str(tmp_path / "empty.sqlite"))
    assert result.exit_code == 1
    assert "No import runs" in result.output


# --------------------------------------------------------------------------- social accounts


def mastodon_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "masto", "platform": "mastodon", "name": "Agenda Mastodon", "adapter": "mastodon",
        "handle": "agenda@mastodon.example", "acquisition_mode": "rss",
        "verification_state": "verified", "enabled": True, "islands": ["Tenerife"],
        "year_policy": "infer",
    }  # fmt: skip
    row.update(overrides)
    return row


FEED_XML = (
    "<?xml version='1.0'?><rss version='2.0'><channel><title>Agenda</title>"
    "<item><guid>https://mastodon.example/@agenda/1</guid>"
    "<link>https://mastodon.example/@agenda/1</link>"
    "<pubDate>Wed, 14 Oct 2026 10:00:00 +0000</pubDate>"
    "<description>&lt;p&gt;Concierto en Adeje el martes 20 de octubre a las 20:00&lt;/p&gt;"
    "</description></item></channel></rss>"
)


@respx.mock
def test_import_events_runs_verified_social_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "make_client",
        lambda *_a, **_k: SafeHttpClient(
            resolver=lambda _h: ["93.184.216.34"], min_interval=0, retries=0, sleep=lambda _s: None
        ),
    )
    feed = respx.get("https://mastodon.example/@agenda.rss").mock(
        return_value=httpx.Response(200, content=FEED_XML.encode())
    )
    config = write_config(tmp_path, [fake_source("a")])
    social = tmp_path / "social.yaml"
    rows = [
        mastodon_row(),
        mastodon_row(id="cand", handle="c@mastodon.example", verification_state="candidate"),
        {"id": "fb", "platform": "facebook", "name": "Facebook", "kind": "platform",
         "acquisition_mode": "requires_authorization"},
    ]  # fmt: skip
    social.write_text(yaml.safe_dump({"sources": rows}), encoding="utf-8")

    result = invoke(
        *import_args(tmp_path, config, "--from", "2026-10-19", "--to", "2026-10-25"),
        "--social-config", str(social),
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert feed.call_count == 1  # the candidate and the Facebook row are never fetched
    report = json.loads((tmp_path / "out" / "import-report.json").read_text(encoding="utf-8"))
    assert report["sources_succeeded"] == ["a", "masto"]
    events = json.loads((tmp_path / "out" / "events.json").read_text(encoding="utf-8"))
    assert "Concierto en Adeje el martes 20 de octubre a las 20:00" in {e["title"] for e in events}


@pytest.mark.parametrize(
    "content",
    ["sources: [unterminated", "- just\n- a list\n", "sources:\n  - id: x\n    bogus: 1\n"],
)
def test_a_broken_social_registry_does_not_stop_official_imports(
    tmp_path: Path, content: str
) -> None:
    config = write_config(tmp_path, [fake_source("a")])
    social = tmp_path / "social.yaml"
    social.write_text(content, encoding="utf-8")

    result = invoke(
        *import_args(tmp_path, config, "--from", "2026-10-19", "--to", "2026-10-25"),
        "--social-config", str(social),
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert "social sources skipped" in result.output
    report = json.loads((tmp_path / "out" / "import-report.json").read_text(encoding="utf-8"))
    assert report["sources_succeeded"] == ["a"]
    warnings = [w for w in report["warnings"] if w["source"] == "social_sources"]
    assert len(warnings) == 1 and "social sources skipped" in warnings[0]["warning"]
    assert str(social) in warnings[0]["warning"]


def test_a_social_id_clashing_with_sources_yaml_is_refused(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("masto")])
    social = tmp_path / "social.yaml"
    social.write_text(yaml.safe_dump({"sources": [mastodon_row()]}), encoding="utf-8")
    result = invoke(*import_args(tmp_path, config), "--social-config", str(social))
    assert result.exit_code == 2 and "masto" in result.output


# --------------------------------------------------------------------------- discover-sources


@respx.mock
def test_discover_sources_writes_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def offline_client(*_a: Any, **_k: Any) -> SafeHttpClient:
        return SafeHttpClient(
            resolver=lambda _h: ["93.184.216.34"], min_interval=0, retries=0, sleep=lambda _s: None
        )

    monkeypatch.setattr(cli, "make_client", offline_client)
    ckan = httpx.Response(200, json={"success": True, "result": {"count": 2, "results": []}})
    for host in ("datos.canarias.es", "datos.tenerife.es", "www.santacruzdetenerife.es"):
        respx.route(host=host).mock(return_value=ckan)
    respx.route(host="datos.gob.es").mock(
        return_value=httpx.Response(200, json={"result": {"items": []}})
    )
    respx.route(host="probe.test").mock(return_value=httpx.Response(404))
    config = write_config(tmp_path, [fake_source("a")], discovery_probes=["probe.test"])

    result = invoke(
        "discover-sources", "--config", str(config), "--output-dir", str(tmp_path / "o"),
        "--db", str(tmp_path / "db.sqlite"),
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    report = json.loads((tmp_path / "o" / "discovery-report.json").read_text(encoding="utf-8"))
    kinds = {r["kind"] for r in report["results"]}
    assert kinds == {"ckan_search", "datos_gob_es", "tribe_events", "ical", "drupal_jsonapi"}
    assert "manual check" in result.output


def discover_args(tmp_path: Path, config: Path, *extra: str) -> list[str]:
    return [
        "discover-sources", "--config", str(config), "--output-dir", str(tmp_path / "o"),
        "--db", str(tmp_path / "db.sqlite"), *extra,
    ]  # fmt: skip


def candidates(tmp_path: Path) -> dict[str, Any]:
    with Database(tmp_path / "db.sqlite") as db:
        return {c.url: c for c in db.list_candidates()}


def test_manual_and_social_urls_are_stored_as_candidates_only(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a")])
    urls = tmp_path / "urls.txt"
    urls.write_text("# organizers\nhttps://c.example.org/agenda\n\n", encoding="utf-8")
    result = invoke(
        *discover_args(
            tmp_path, config, "--no-catalogs", "--no-links",
            "--url", "https://a.example.org/eventos", "--url", "https://www.facebook.com/grupo",
            "--url-file", str(urls), "--social-url", "https://mastodon.example/@agenda",
        )
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    found = candidates(tmp_path)
    assert set(found) == {
        "https://a.example.org/eventos", "https://www.facebook.com/grupo",
        "https://c.example.org/agenda", "https://mastodon.example/@agenda",
    }  # fmt: skip
    assert found["https://a.example.org/eventos"].kind == "web"
    assert found["https://www.facebook.com/grupo"].kind == "social"
    assert found["https://www.facebook.com/grupo"].acquisition_mode == "requires_authorization"
    mastodon = found["https://mastodon.example/@agenda"]
    assert (mastodon.kind, mastodon.discovered_via, mastodon.acquisition_mode) == (
        "social", "user_reported", "rss",
    )  # fmt: skip
    assert all(c.verification_state == "candidate" for c in found.values())
    with Database(tmp_path / "db.sqlite") as db:
        assert db.count("sources") == 0  # nothing is enabled or registered
    report = json.loads((tmp_path / "o" / "discovery-report.json").read_text(encoding="utf-8"))
    assert len(report["candidates"]) == 4 and report["summary"]["new_candidates"] == 4

    again = invoke(
        *discover_args(tmp_path, config, "--no-catalogs", "--no-links", "--url", "https://a.example.org/eventos")
    )  # fmt: skip
    assert again.exit_code == 0 and "0 new" in again.output


@pytest.mark.parametrize(
    "extra", [["--url", "ftp://x.test/a"], ["--social-url", "not a url"], ["--url", "http://"]]
)
def test_invalid_candidate_urls_are_refused_before_anything_runs(
    tmp_path: Path, extra: list[str]
) -> None:
    config = write_config(tmp_path, [fake_source("a")])
    result = invoke(*discover_args(tmp_path, config, "--no-catalogs", "--no-links", *extra))
    assert result.exit_code == 2
    assert not (tmp_path / "db.sqlite").exists()


@respx.mock
@pytest.mark.usefixtures("offline")
def test_discovery_follows_links_of_configured_sources(tmp_path: Path) -> None:
    respx.get("https://www.example.org/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://www.example.org/").mock(
        return_value=httpx.Response(
            200,
            text='<a href="/agenda/">Agenda</a><a href="https://elsewhere.test/agenda">Agenda</a>',
            headers={"content-type": "text/html"},
        )
    )
    config = write_config(tmp_path, [fake_source("portal", homepage="https://www.example.org/")])
    result = invoke(*discover_args(tmp_path, config, "--no-catalogs"))
    assert result.exit_code == 0, result.output
    found = candidates(tmp_path)
    assert set(found) == {"https://www.example.org/agenda/"}
    assert found["https://www.example.org/agenda/"].discovered_via == "link:portal"


def test_list_sources_candidates_prints_them(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a")])
    invoke(
        *discover_args(
            tmp_path,
            config,
            "--no-catalogs",
            "--no-links",
            "--url",
            "https://a.example.org/eventos",
        )
    )
    result = invoke(
        "list-sources", "--candidates", "--config", str(config), "--db", str(tmp_path / "db.sqlite")
    )
    assert result.exit_code == 0, result.output
    assert "https://a.example.org/eventos" in result.output and "candidate" in result.output


# --------------------------------------------------------------------------- misc


def test_module_is_runnable() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "canarias_events.cli", "--help"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert proc.returncode == 0
    assert "import-events" in proc.stdout


def test_log_level_option_is_validated(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a")])
    result = invoke("--log-level", "loud", "list-sources", "--config", str(config))
    assert result.exit_code == 2


def test_source_config_type_is_used() -> None:
    assert SourceConfig.model_validate(fake_source("a")).adapter == "fake"


# --------------------------------------------------------------------------- review-quarantine


def quarantined_db(tmp_path: Path) -> tuple[Path, str]:
    """A database with one source whose latest run was anomalous (3 events held back)."""
    config = write_config(tmp_path, [fake_source("a", params={"titles": ["Uno", "Dos", "Tres"]})])
    db_path = tmp_path / "db.sqlite"
    with Database(db_path) as db:
        for n in range(1, 4):
            db.record_source_run(
                n, "a", NOW, status="succeeded", source_status="ok", events_found=100,
                events_in_window=100, duration_s=1.0, error=None, strategy=None,
                extraction_ok=None, listing_hash=None, anomalies=[],
            )  # fmt: skip
    result = invoke(*import_args(tmp_path, config, "--from", "2026-10-19", "--to", "2026-10-25"))
    assert result.exit_code == 1, result.output  # the only source was anomalous
    return config, str(db_path)


def test_review_quarantine_lists_and_accepts(tmp_path: Path) -> None:
    config, db = quarantined_db(tmp_path)
    events = tmp_path / "out" / "events.json"
    assert json.loads(events.read_text(encoding="utf-8")) == []  # doubtful data not exported

    listing = invoke("review-quarantine", "--db", db, "--list")
    assert listing.exit_code == 0, listing.output
    assert "a" in listing.output and "count_drop" in listing.output and "3" in listing.output

    assert invoke("review-quarantine", "--db", db, "--accept", "nope").exit_code == 1
    accepted = invoke("review-quarantine", "--db", db, "--accept", "a")
    assert accepted.exit_code == 0, accepted.output
    assert "Accepted 3" in accepted.output
    assert "No quarantined" in invoke("review-quarantine", "--db", db, "--list").output

    again = invoke(*import_args(tmp_path, config, "--from", "2026-10-19", "--to", "2026-10-25"))
    assert again.exit_code in (0, 1)
    with Database(db) as check:
        assert check.count("events") == 3


def test_review_quarantine_requires_an_action(tmp_path: Path) -> None:
    result = invoke("review-quarantine", "--db", str(tmp_path / "x.sqlite"))
    assert result.exit_code != 0


# --------------------------------------------------------------------------- import options


def test_dry_run_changes_nothing_on_disk(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a")])
    window = ("--from", "2026-10-19", "--to", "2026-10-25")
    assert invoke(*import_args(tmp_path, config, *window)).exit_code == 0
    db_file, events_file = tmp_path / "db.sqlite", tmp_path / "out" / "events.json"
    before = (db_file.read_bytes(), events_file.read_bytes())

    changed = write_config(tmp_path, [fake_source("a", params={"titles": ["Nuevo", "Otro"]})])
    result = invoke(*import_args(tmp_path, changed, *window, "--dry-run"))

    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert (db_file.read_bytes(), events_file.read_bytes()) == before
    report = json.loads((tmp_path / "out" / "dry-run-report.json").read_text(encoding="utf-8"))
    assert report["dry_run"] is True
    assert (report["totals"]["new"], report["totals"]["updated"]) == (1, 1)  # honest counts
    assert not (tmp_path / "out" / "snapshots").exists()


def test_dry_run_creates_no_database(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a")])
    result = invoke(*import_args(tmp_path, config, "--dry-run"))
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "db.sqlite").exists()
    assert not (tmp_path / "out" / "events.json").exists()
    assert (tmp_path / "out" / "dry-run-report.json").exists()


def capture_run_import(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    real = run_import

    def spy(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr("canarias_events.cli.run_import", spy)
    return calls


def test_no_ai_disables_the_model_whatever_the_config_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = capture_run_import(monkeypatch)
    config = write_config(tmp_path, [fake_source("a")], ai={"enabled": True})
    invoke(*import_args(tmp_path, config))
    invoke(*import_args(tmp_path, config, "--no-ai", "--force"))
    assert [c["ai"].enabled for c in calls] == [True, False]


def test_force_ignores_the_source_frequency(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a", frequency="weekly")])
    assert invoke(*import_args(tmp_path, config)).exit_code == 0
    second = invoke(*import_args(tmp_path, config))
    assert "skipped a: frequency weekly" in second.output
    forced = invoke(*import_args(tmp_path, config, "--force"))
    assert forced.exit_code == 0, forced.output
    assert "skipped" not in forced.output


def test_low_resource_flag_and_config_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = capture_run_import(monkeypatch)
    config = write_config(tmp_path, [fake_source("a")])
    invoke(*import_args(tmp_path, config, "--low-resource"))
    assert calls[-1]["low_resource"] is True
    invoke(*import_args(tmp_path, config, "--force"))
    assert calls[-1]["low_resource"] is False


def test_the_report_shows_timings(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a")])
    result = invoke(*import_args(tmp_path, config))
    assert "Timing: total" in result.output


# --------------------------------------------------------------------------- import-url


EVENT_URL = "https://www.eventbrite.es/e/concierto-de-gala-tickets-123456"


def event_page_html(name: str = "Concierto de gala") -> str:
    data = {
        "@context": "https://schema.org", "@type": "Event", "name": name,
        "startDate": "2026-10-24T20:30:00+01:00",
        "location": {"@type": "Place", "name": "Teatro Leal", "address": "La Laguna"},
    }  # fmt: skip
    return (
        f'<html><body><script type="application/ld+json">{json.dumps(data)}</script></body></html>'
    )


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "make_client",
        lambda *_a, **_k: SafeHttpClient(
            resolver=lambda _h: ["93.184.216.34"], min_interval=0, retries=0, sleep=lambda _s: None
        ),
    )


def serve_event_page(url: str = EVENT_URL, name: str = "Concierto de gala") -> None:
    host = url.split("/")[2]
    respx.get(f"https://{host}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(url).mock(
        return_value=httpx.Response(
            200, text=event_page_html(name), headers={"content-type": "text/html"}
        )
    )


def pending_titles(db: Path) -> list[str]:
    with Database(db) as database:
        return [i.event.title for i in database.quarantine_pending()]


@respx.mock
@pytest.mark.usefixtures("offline")
def test_import_url_dry_run_prints_the_event_and_stores_nothing(tmp_path: Path) -> None:
    serve_event_page()
    db = tmp_path / "db.sqlite"
    result = invoke("import-url", EVENT_URL, "--dry-run", "--db", str(db))
    assert result.exit_code == 0, result.output
    start = result.output.index("[")
    shown = json.loads(result.output[start : result.output.rindex("]") + 1])
    assert shown[0]["title"] == "Concierto de gala"
    assert shown[0]["extra"]["provenance"] == "import_url"
    assert "raw_data" not in shown[0]
    assert not db.exists()


@respx.mock
@pytest.mark.usefixtures("offline")
def test_import_url_holds_the_event_for_review_until_accepted(tmp_path: Path) -> None:
    serve_event_page()
    db = tmp_path / "db.sqlite"
    result = invoke("import-url", EVENT_URL, "--db", str(db))
    assert result.exit_code == 0, result.output
    assert "review-quarantine" in result.output
    with Database(db) as database:
        assert database.count("events") == 0
    assert pending_titles(db) == ["Concierto de gala"]

    listing = invoke("review-quarantine", "--list", "--db", str(db))
    assert "import_url" in listing.output and "needs_review" in listing.output

    accepted = invoke("review-quarantine", "--accept", "import_url", "--db", str(db))
    assert accepted.exit_code == 0, accepted.output
    with Database(db) as database:
        [event] = database.all_events()
        assert event.title == "Concierto de gala" and event.source_type == "import_url"
        assert database.recent_source_runs("import_url", 3) == []


@respx.mock
@pytest.mark.usefixtures("offline")
def test_import_url_accept_publishes_at_once(tmp_path: Path) -> None:
    serve_event_page()
    db = tmp_path / "db.sqlite"
    result = invoke("import-url", EVENT_URL, "--accept", "--db", str(db))
    assert result.exit_code == 0, result.output
    with Database(db) as database:
        assert [e.title for e in database.all_events()] == ["Concierto de gala"]
    assert pending_titles(db) == []


@respx.mock
@pytest.mark.usefixtures("offline")
def test_two_imports_wait_side_by_side(tmp_path: Path) -> None:
    other = "https://tickets.example.org/e/otro"
    serve_event_page()
    serve_event_page(other, "Otro concierto")
    db = tmp_path / "db.sqlite"
    invoke("import-url", EVENT_URL, "--db", str(db))
    invoke("import-url", other, "--db", str(db))
    invoke("import-url", EVENT_URL, "--db", str(db))  # again: not repeated
    assert pending_titles(db) == ["Concierto de gala", "Otro concierto"]


@respx.mock
@pytest.mark.usefixtures("offline")
@pytest.mark.parametrize(
    "url", ["https://www.facebook.com/events/1", "https://www.instagram.com/p/abc/"]
)
def test_import_url_refuses_facebook_and_instagram(tmp_path: Path, url: str) -> None:
    db = tmp_path / "db.sqlite"
    result = invoke("import-url", url, "--db", str(db))
    assert result.exit_code == 2
    assert "submit-event" in result.output
    assert respx.calls.call_count == 0 and not db.exists()


@respx.mock
@pytest.mark.usefixtures("offline")
def test_import_url_reports_pages_it_cannot_read(tmp_path: Path) -> None:
    respx.get("https://www.eventbrite.es/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /e/\n")
    )
    result = invoke("import-url", EVENT_URL, "--db", str(tmp_path / "db.sqlite"))
    assert result.exit_code == 1
    assert "robots_disallowed" in result.output


@pytest.mark.usefixtures("offline")
def test_import_url_rejects_non_http_urls(tmp_path: Path) -> None:
    result = invoke("import-url", "ftp://example.org/x", "--db", str(tmp_path / "db.sqlite"))
    assert result.exit_code == 2


# --------------------------------------------------------------------------- submit-event


ORGANIZER_EVENT: dict[str, Any] = {
    "title": "Taller de cerámica",
    "start": "2026-10-24T18:00:00",
    "organizer_name": "Asociación Alfareros",
    "municipality": "Adeje",
}


def submission_file(tmp_path: Path, data: Any, name: str = "event.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_submit_event_holds_the_event_until_accepted(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    file = submission_file(tmp_path, ORGANIZER_EVENT)
    result = invoke("submit-event", "--file", str(file), "--db", str(db))
    assert result.exit_code == 0, result.output
    assert "review-quarantine" in result.output
    assert pending_titles(db) == ["Taller de cerámica"]

    listing = invoke("review-quarantine", "--list", "--db", str(db))
    assert "organizer_submission" in listing.output
    assert (
        invoke("review-quarantine", "--accept", "organizer_submission", "--db", str(db)).exit_code
        == 0
    )
    with Database(db) as database:
        [event] = database.all_events()
        assert event.source_type == "organizer_submission" and event.island == "Tenerife"


def test_submit_event_dry_run_and_accept(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    file = submission_file(tmp_path, ORGANIZER_EVENT)
    dry = invoke("submit-event", "--file", str(file), "--dry-run", "--db", str(db))
    assert dry.exit_code == 0 and "Taller de cerámica" in dry.output and not db.exists()

    done = invoke("submit-event", "--file", str(file), "--accept", "--db", str(db))
    assert done.exit_code == 0, done.output
    with Database(db) as database:
        assert [e.title for e in database.all_events()] == ["Taller de cerámica"]


def test_submit_event_rejects_an_invalid_file_and_stores_nothing(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    file = submission_file(tmp_path, [ORGANIZER_EVENT, {**ORGANIZER_EVENT, "title": ""}])
    result = invoke("submit-event", "--file", str(file), "--db", str(db))
    assert result.exit_code == 2
    assert "event 2: title" in result.output
    assert not db.exists()


def test_submit_event_reads_ics_with_the_organizer_name(tmp_path: Path) -> None:
    ics = tmp_path / "event.ics"
    ics.write_text(
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//EN\nBEGIN:VEVENT\nUID:x@y\n"
        "DTSTAMP:20261001T100000Z\nDTSTART;TZID=Atlantic/Canary:20261024T180000\n"
        "SUMMARY:Taller\nLOCATION:Casa de la Cultura\\, Adeje\nEND:VEVENT\nEND:VCALENDAR\n",
        encoding="utf-8",
    )
    db = tmp_path / "db.sqlite"
    missing = invoke("submit-event", "--file", str(ics), "--db", str(db))
    assert missing.exit_code == 2 and "--organizer" in missing.output
    ok = invoke("submit-event", "--file", str(ics), "--organizer", "Alfareros", "--db", str(db))
    assert ok.exit_code == 0, ok.output
    assert pending_titles(db) == ["Taller"]


def test_the_shipped_schema_and_example_match_the_model() -> None:
    from canarias_events.submissions import OrganizerEvent, load_submission

    docs = Path(__file__).parent.parent / "docs"
    schema = json.loads((docs / "organizer-submission.schema.json").read_text(encoding="utf-8"))
    assert schema == OrganizerEvent.model_json_schema()
    example = docs / "organizer-submission.example.json"
    [event] = load_submission(example, organizer=None, now=NOW)
    assert event.source_type == "organizer_submission"


# --------------------------------------------------------------------------- validate-source


SOURCE_URL = "https://agenda.example.org/programacion/"


def serve_source(days: int = 3) -> None:
    start = datetime(2026, 10, 15, 20, 0, tzinfo=TZ) + timedelta(days=days)
    data = {
        "@context": "https://schema.org", "@type": "Event", "name": "Concierto",
        "startDate": start.isoformat(),
        "location": {"@type": "Place", "name": "Teatro", "address": "La Laguna"},
    }  # fmt: skip
    respx.get("https://agenda.example.org/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(SOURCE_URL).mock(
        return_value=httpx.Response(
            200,
            text=f'<script type="application/ld+json">{json.dumps(data)}</script>',
            headers={"content-type": "text/html"},
        )
    )


@respx.mock
@pytest.mark.usefixtures("offline")
def test_validate_source_prints_a_scored_report_and_stores_nothing(tmp_path: Path) -> None:
    serve_source()
    db = tmp_path / "db.sqlite"
    result = invoke("validate-source", SOURCE_URL, "--db", str(db), "--config", str(tmp_path / "x"))
    assert result.exit_code == 0, result.output
    assert "Score:" in result.output and "acquisition_mode=html" in result.output
    assert "Not saved" in result.output
    assert not db.exists()


@respx.mock
@pytest.mark.usefixtures("offline")
def test_validate_source_save_updates_the_candidate_state(tmp_path: Path) -> None:
    serve_source()
    db = tmp_path / "db.sqlite"
    config = write_config(tmp_path, [fake_source("a")])
    invoke(*discover_args(tmp_path, config, "--no-catalogs", "--no-links", "--url", SOURCE_URL))
    with Database(db) as database:
        [row] = database.list_candidates()
    assert row.verification_state == "candidate"

    plain = invoke("validate-source", row.id, "--db", str(db), "--config", str(config))
    assert plain.exit_code == 0, plain.output
    with Database(db) as database:
        assert database.list_candidates()[0].verification_state == "candidate"  # no --save: as was

    saved = invoke("validate-source", row.id, "--save", "--db", str(db), "--config", str(config))
    assert saved.exit_code == 0, saved.output
    with Database(db) as database:
        [after] = database.list_candidates()
        assert after.verification_state == "verified" and after.acquisition_mode == "html"
        assert after.last_score is not None and after.last_score > 0
        assert after.report is not None and after.report["events_found"] == 1


@respx.mock
@pytest.mark.usefixtures("offline")
def test_validate_source_save_creates_the_candidate_for_a_new_url(tmp_path: Path) -> None:
    serve_source()
    db = tmp_path / "db.sqlite"
    result = invoke("validate-source", SOURCE_URL, "--save", "--db", str(db), "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output[: result.output.rindex("}") + 1])["events_found"] == 1
    with Database(db) as database:
        [row] = database.list_candidates()
        assert row.url == SOURCE_URL and row.discovered_via == "validate-source"
        assert row.verification_state == "verified"


@respx.mock
@pytest.mark.usefixtures("offline")
def test_validate_source_accepts_a_configured_source_id(tmp_path: Path) -> None:
    respx.get("https://feeds.example.org/e.ics").mock(
        return_value=httpx.Response(
            200,
            text=(
                "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//EN\nBEGIN:VEVENT\nUID:a@b\n"
                "DTSTAMP:20261001T100000Z\nDTSTART:20261020T180000Z\nSUMMARY:Verbena\n"
                "LOCATION:Plaza\\, Adeje\nEND:VEVENT\nEND:VCALENDAR\n"
            ),
            headers={"content-type": "text/calendar"},
        )
    )
    config = write_config(
        tmp_path,
        [
            {
                "id": "ics1",
                "name": "Ics",
                "adapter": "ical",
                "url": "https://feeds.example.org/e.ics",
            }
        ],
    )
    result = invoke("validate-source", "ics1", "--config", str(config), "--db", str(tmp_path / "d"))
    assert result.exit_code == 0, result.output
    assert "[ics1]" in result.output and "Events:       1" in result.output


def test_validate_source_rejects_unknown_targets(tmp_path: Path) -> None:
    config = write_config(tmp_path, [fake_source("a")])
    result = invoke("validate-source", "nope", "--config", str(config), "--db", str(tmp_path / "d"))
    assert result.exit_code == 2


def test_validate_source_social_url_needs_no_network(tmp_path: Path) -> None:
    result = invoke(
        "validate-source", "https://www.facebook.com/grupo", "--db", str(tmp_path / "d"),
        "--config", str(tmp_path / "x"),
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "requires_authorization" in result.output and "rejected" in result.output


@respx.mock
@pytest.mark.usefixtures("offline")
@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/evento", "http://10.0.0.5/e", "http://169.254.169.254/latest"]
)
def test_import_url_refuses_private_addresses_cleanly(tmp_path: Path, url: str) -> None:
    db = tmp_path / "db.sqlite"
    result = invoke("import-url", url, "--db", str(db))
    assert result.exit_code == 2, result.output
    assert isinstance(result.exception, SystemExit)  # a clean exit, not a traceback
    assert respx.calls.call_count == 0 and not db.exists()


@respx.mock
@pytest.mark.usefixtures("offline")
def test_import_url_refuses_a_pdf_cleanly(tmp_path: Path) -> None:
    url = "https://www.example.org/programa.pdf"
    respx.get("https://www.example.org/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(url).mock(
        return_value=httpx.Response(
            200, content=b"%PDF-1.7 ...", headers={"content-type": "application/pdf"}
        )
    )
    db = tmp_path / "db.sqlite"
    result = invoke("import-url", url, "--db", str(db))
    assert result.exit_code == 2, result.output
    assert isinstance(result.exception, SystemExit)
    assert "pdf" in result.output.lower() and not db.exists()


def verified_candidate(tmp_path: Path, config: Path) -> tuple[Path, str]:
    """A candidate that an earlier validation marked verified (html, score 80)."""
    db_path = tmp_path / "db.sqlite"
    with Database(db_path) as db:
        db.add_candidate(SOURCE_URL, "web", "manual", NOW)
        saved = db.get_candidate(SOURCE_URL)
        assert saved is not None
        db.update_candidate(
            saved.id, state="verified", mode="html", score=80, report={"old": True}, now=NOW
        )
    return db_path, saved.id


@respx.mock
@pytest.mark.usefixtures("offline")
@pytest.mark.parametrize("status", [429, 503])
def test_validate_source_save_keeps_the_state_after_a_transient_failure(
    tmp_path: Path, status: int
) -> None:
    respx.get("https://agenda.example.org/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(SOURCE_URL).mock(return_value=httpx.Response(status, text="later"))
    config = write_config(tmp_path, [fake_source("a")])
    db_path, cid = verified_candidate(tmp_path, config)

    result = invoke("validate-source", cid, "--save", "--db", str(db_path), "--config", str(config))

    assert result.exit_code == 0, result.output
    with Database(db_path) as db:
        row = db.get_candidate(cid)
    assert row is not None
    assert (row.verification_state, row.acquisition_mode, row.last_score) == (
        "verified",
        "html",
        80,
    )
    assert row.last_validated_at != NOW.isoformat()  # last_checked moved
    assert row.report is not None and str(status) in row.report["error"]
    assert "unchanged" in result.output


@respx.mock
@pytest.mark.usefixtures("offline")
@pytest.mark.parametrize("status", [404, 410])
def test_validate_source_save_rejects_a_page_that_is_gone(tmp_path: Path, status: int) -> None:
    respx.get("https://agenda.example.org/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(SOURCE_URL).mock(return_value=httpx.Response(status, text="gone"))
    config = write_config(tmp_path, [fake_source("a")])
    db_path, cid = verified_candidate(tmp_path, config)

    result = invoke("validate-source", cid, "--save", "--db", str(db_path), "--config", str(config))

    assert result.exit_code == 0, result.output
    with Database(db_path) as db:
        row = db.get_candidate(cid)
    assert row is not None
    assert (row.verification_state, row.acquisition_mode, row.last_score) == ("rejected", None, 0)
    assert row.report is not None and row.report["error"] is None
    assert f"HTTP {status}" in " ".join(row.report["notes"])


@respx.mock
@pytest.mark.usefixtures("offline")
def test_validate_source_reports_a_parser_error_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def explode(self: object, body: bytes, window: object) -> None:
        raise ValueError("bad calendar")

    monkeypatch.setattr(IcalAdapter, "read", explode)
    respx.get("https://agenda.example.org/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(SOURCE_URL).mock(
        return_value=httpx.Response(
            200, text="BEGIN:VCALENDAR\nEND:VCALENDAR\n", headers={"content-type": "text/calendar"}
        )
    )
    result = invoke(
        "validate-source", SOURCE_URL, "--save", "--db", str(tmp_path / "db.sqlite"),
        "--config", str(tmp_path / "x"),
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert isinstance(result.exception, (SystemExit, type(None)))
    assert "Error:" in result.output and "bad calendar" in result.output
    with Database(tmp_path / "db.sqlite") as db:
        [row] = db.list_candidates()
    assert row.verification_state == "candidate" and row.last_score is None


def test_validation_output_treats_an_unknown_error_rate_as_unknown(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from canarias_events.validation import ValidationReport

    report = ValidationReport(url=SOURCE_URL, reachable=True)
    report.reliability = {"runs": 0, "error_rate": None, "last_status": None}
    cli._echo_validation(report)
    assert "unknown" in capsys.readouterr().out.split("Reliability:")[1].splitlines()[0]
