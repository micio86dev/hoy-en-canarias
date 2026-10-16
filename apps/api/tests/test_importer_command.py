import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path

from hoy_api.importer import build_import_argv, format_stderr_tail
from hoy_api.logging_setup import JsonFormatter
from hoy_api.worker import ImportWindow


def test_import_argv_passes_window_database_and_disables_ai() -> None:
    window = ImportWindow(start=date(2026, 10, 16), end=date(2026, 12, 15))

    argv = build_import_argv("canarias-events", window, Path("/data/events.sqlite"))

    assert argv == [
        "canarias-events",
        "import-events",
        "--from",
        "2026-10-16",
        "--to",
        "2026-12-15",
        "--db",
        "/data/events.sqlite",
        "--no-ai",
    ]


def test_stderr_tail_keeps_only_the_last_lines() -> None:
    stderr = "\n".join(f"line {n}" for n in range(1, 20))

    assert format_stderr_tail(stderr).splitlines() == [f"line {n}" for n in range(15, 20)]


def test_json_formatter_emits_structured_fields() -> None:
    record = logging.LogRecord(
        "hoy_api.worker", logging.INFO, __file__, 1, "import succeeded", None, None
    )
    record.run_id = 7

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "hoy_api.worker"
    assert payload["message"] == "import succeeded"
    assert payload["run_id"] == 7
    assert datetime.fromisoformat(payload["ts"]).tzinfo == UTC


def test_window_starts_on_the_canary_calendar_day_not_utc() -> None:
    from hoy_api.importer import window_from

    # 23:30 UTC on 15 October is already 16 October in the Canary Islands.
    window = window_from(datetime(2026, 10, 15, 23, 30, tzinfo=UTC), days=60)

    assert window.start == date(2026, 10, 16)
    assert window.end == date(2026, 12, 15)
