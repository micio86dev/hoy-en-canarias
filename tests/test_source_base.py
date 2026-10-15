from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from canarias_events.models import Event
from canarias_events.sources import registry
from canarias_events.sources.base import (
    FetchContext,
    SourceAdapter,
    SourceConfig,
    SourceFetchResult,
    as_float,
    clean_text,
    municipality_in_text,
    resolve_island,
)


def config(**overrides: object) -> SourceConfig:
    data: dict[str, object] = {
        "id": "demo",
        "name": "Demo",
        "adapter": "demo",
        "url": "https://example.org/api",
    }
    data.update(overrides)
    return SourceConfig.model_validate(data)


# --------------------------------------------------------------------------- SourceConfig


def test_config_defaults() -> None:
    cfg = config()
    assert cfg.enabled is True
    assert cfg.islands == []
    assert cfg.categories == []
    assert cfg.params == {}
    assert cfg.municipality is None
    assert cfg.license is None
    assert cfg.auth_env is None


def test_config_requires_id_and_url() -> None:
    with pytest.raises(ValidationError):
        SourceConfig.model_validate({"name": "x", "adapter": "a"})


def test_config_rejects_non_http_url() -> None:
    with pytest.raises(ValidationError):
        config(url="file:///etc/passwd")


def test_config_rejects_unknown_island() -> None:
    with pytest.raises(ValidationError):
        config(islands=["Atlantis"])


def test_config_normalizes_island_names() -> None:
    assert config(islands=["isla de tenerife", "gomera"]).islands == ["Tenerife", "La Gomera"]


# --------------------------------------------------------------------------- result


def test_result_defaults_are_independent() -> None:
    a, b = SourceFetchResult(), SourceFetchResult()
    a.warnings.append("x")
    assert b.warnings == []
    assert a.events == [] and a.records_received == 0 and a.pages == 0 and a.discarded == []


def test_result_discard_records_reason() -> None:
    result = SourceFetchResult()
    result.discard("missing title", "42")
    assert result.discarded == [("missing title", "42")]


def test_adapter_is_abstract() -> None:
    with pytest.raises(TypeError):
        SourceAdapter(config())  # type: ignore[abstract]


def test_adapter_subclass_works() -> None:
    class Dummy(SourceAdapter):
        source_type = "dummy"

        def fetch(self, ctx: FetchContext) -> SourceFetchResult:
            return SourceFetchResult(events=[Event(title="t", source_name="Demo")], pages=1)

    adapter = Dummy(config())
    assert adapter.config.id == "demo"
    assert adapter.fetch(None).pages == 1  # type: ignore[arg-type]


# --------------------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    "value", [None, "", "  ", "-", "--", "---", "Por determinar", "N/A", "null"]
)
def test_clean_text_placeholders_become_none(value: str | None) -> None:
    assert clean_text(value) is None


def test_clean_text_strips_and_passes_numbers() -> None:
    assert clean_text("  Hola  ") == "Hola"
    assert clean_text(12) == "12"
    assert clean_text(None) is None


def test_resolve_island_explicit_field_wins() -> None:
    cfg = config(islands=["Tenerife"], municipality="Candelaria")
    assert resolve_island(cfg, island="La Palma", municipality="Candelaria") == "La Palma"


def test_resolve_island_item_municipality_before_config_municipality() -> None:
    cfg = config(municipality="Antigua")
    assert resolve_island(cfg, municipality="Arona") == "Tenerife"


def test_resolve_island_config_municipality() -> None:
    assert resolve_island(config(municipality="Antigua")) == "Fuerteventura"


def test_resolve_island_coordinates() -> None:
    cfg = config(islands=["Tenerife", "Gran Canaria"])
    assert resolve_island(cfg, latitude=28.4636, longitude=-16.2518) == "Tenerife"


def test_resolve_island_single_configured_island() -> None:
    assert resolve_island(config(islands=["Lanzarote"])) == "Lanzarote"


def test_resolve_island_multiple_configured_islands_is_none() -> None:
    assert resolve_island(config(islands=["Lanzarote", "Tenerife"])) is None


def test_resolve_island_unknown_is_none() -> None:
    assert resolve_island(config(), island="Atlantis", municipality="Nowhere") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("28.5", 28.5), (3, 3.0), (" -16.2 ", -16.2), (None, None), (True, None), ("x", None),
     ("nan", None), ("inf", None), (float("nan"), None)],
)  # fmt: skip
def test_as_float_is_finite_or_none(value: object, expected: float | None) -> None:
    assert as_float(value) == expected


def test_municipality_in_text_uses_last_known_part() -> None:
    text = "Pabellón Municipal, Rambla de los Menceyes, 27, Candelaria, Tenerife"
    assert municipality_in_text(text) == "Candelaria"


@pytest.mark.parametrize("text", [None, "", "Plaza Mayor", "Somewhere, Nowhere"])
def test_municipality_in_text_unknown_is_none(text: str | None) -> None:
    assert municipality_in_text(text) is None


# --------------------------------------------------------------------------- registry


def test_load_sources_reads_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Dummy(SourceAdapter):
        source_type = "dummy"

        def fetch(self, ctx: FetchContext) -> SourceFetchResult:
            return SourceFetchResult()

    monkeypatch.setitem(registry.ADAPTERS, "demo", Dummy)
    path = tmp_path / "sources.yaml"
    path.write_text(
        "sources:\n"
        "  - id: a\n    name: A\n    adapter: demo\n    url: https://example.org/a\n"
        "    islands: [Tenerife]\n    params: {max_pages: 3}\n"
        "  - id: b\n    name: B\n    adapter: demo\n    url: https://example.org/b\n"
        "    enabled: false\n"
        "candidates:\n"
        "  - name: C\n    url: https://example.org/c\n    reason: html only\n"
        "    checked_at: 2026-10-15\n"
        "excluded:\n"
        "  - name: D\n    url: https://example.org/d\n    reason: closed\n"
        "    checked_at: 2026-10-15\n",
        encoding="utf-8",
    )
    sources = registry.load_sources(path)
    assert [s.id for s in sources] == ["a", "b"]
    assert sources[0].params == {"max_pages": 3}
    assert sources[1].enabled is False
    catalog = registry.load_catalog(path)
    assert [c.name for c in catalog.candidates] == ["C"]
    assert catalog.excluded[0].reason == "closed"
    assert str(catalog.excluded[0].checked_at) == "2026-10-15"
    assert isinstance(registry.create_adapter(sources[0]), Dummy)


def test_load_sources_rejects_unknown_adapter(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "sources:\n  - id: a\n    name: A\n    adapter: nope\n    url: https://example.org\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown adapter 'nope'"):
        registry.load_sources(path)


def test_load_sources_rejects_duplicate_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Dummy(SourceAdapter):
        source_type = "dummy"

        def fetch(self, ctx: FetchContext) -> SourceFetchResult:
            return SourceFetchResult()

    monkeypatch.setitem(registry.ADAPTERS, "demo", Dummy)
    path = tmp_path / "s.yaml"
    entry = "  - id: a\n    name: A\n    adapter: demo\n    url: https://example.org\n"
    path.write_text("sources:\n" + entry + entry, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate source id 'a'"):
        registry.load_sources(path)


def test_load_sources_empty_file_gives_empty_list(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text("", encoding="utf-8")
    assert registry.load_sources(path) == []
