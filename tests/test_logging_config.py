from __future__ import annotations

import io
import logging

import httpx
import pytest

from canarias_events.logging_config import configure_logging


@pytest.fixture(autouse=True)
def restore_logging() -> object:
    root = logging.getLogger()
    before = (root.level, list(root.handlers))
    yield None
    root.setLevel(before[0])
    root.handlers[:] = before[1]
    logging.getLogger("httpx").setLevel(logging.NOTSET)


def test_level_argument_sets_root_level() -> None:
    configure_logging("debug", stream=io.StringIO())
    assert logging.getLogger().level == logging.DEBUG


def test_level_comes_from_environment_when_not_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANARIAS_LOG_LEVEL", "warning")
    configure_logging(None, stream=io.StringIO())
    assert logging.getLogger().level == logging.WARNING
    monkeypatch.delenv("CANARIAS_LOG_LEVEL")
    configure_logging(None, stream=io.StringIO())
    assert logging.getLogger().level == logging.INFO


def test_invalid_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="log level"):
        configure_logging("loud", stream=io.StringIO())


def test_configuring_twice_keeps_one_handler() -> None:
    configure_logging("info", stream=io.StringIO())
    configure_logging("info", stream=io.StringIO())
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_canarias", False)]
    assert len(ours) == 1


def test_lines_are_structured_text() -> None:
    stream = io.StringIO()
    configure_logging("info", stream=stream)
    logging.getLogger("canarias_events.pipeline").info("source %s ok", "demo")
    line = stream.getvalue().strip()
    assert "INFO" in line
    assert "canarias_events.pipeline" in line
    assert line.endswith("source demo ok")


def test_httpx_request_urls_stay_masked() -> None:
    stream = io.StringIO()
    configure_logging("debug", stream=stream)
    logging.getLogger("httpx").info(
        'HTTP Request: %s %s "%s"', "GET", httpx.URL("https://a.test/x?apikey=SECRET"), "200 OK"
    )
    assert "SECRET" not in stream.getvalue()
    assert "apikey=" in stream.getvalue()
