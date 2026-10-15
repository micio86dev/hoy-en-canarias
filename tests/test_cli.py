from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
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
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceConfig,
    SourceFetchResult,
)
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


def test_report_without_runs(tmp_path: Path) -> None:
    result = invoke("report", "--db", str(tmp_path / "empty.sqlite"))
    assert result.exit_code == 1
    assert "No import runs" in result.output


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
        "discover-sources", "--config", str(config), "--output-dir", str(tmp_path / "o")
    )

    assert result.exit_code == 0, result.output
    report = json.loads((tmp_path / "o" / "discovery-report.json").read_text(encoding="utf-8"))
    kinds = {r["kind"] for r in report["results"]}
    assert kinds == {"ckan_search", "datos_gob_es", "tribe_events", "ical", "drupal_jsonapi"}
    assert "manual check" in result.output


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
