from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from canarias_events.geo import ISLANDS
from canarias_events.sources.registry import ADAPTERS, load_catalog, load_sources

CONFIG = Path(__file__).parent.parent / "config" / "sources.yaml"


def test_shipped_config_loads() -> None:
    sources = load_sources(CONFIG)
    ids = [s.id for s in sources]
    assert len(ids) == len(set(ids))
    assert set(ids) >= {
        "holaislascanarias", "cabildo_tenerife_deportes", "deportes_tenerife",
        "deportes_tenerife_ical", "candelaria", "santiago_del_teide", "granadilla_de_abona",
        "antigua", "antigua_ical", "los_silos", "santa_ursula", "valle_gran_rey",
        "ticketmaster", "gobcan_agenda_cultural", "santacruz_agenda_cultural",
    }  # fmt: skip
    assert all(s.adapter in ADAPTERS for s in sources)


def test_enabled_flags() -> None:
    by_id = {s.id: s for s in load_sources(CONFIG)}
    for disabled in (
        "deportes_tenerife_ical",
        "antigua_ical",
        "gobcan_agenda_cultural",
        "santacruz_agenda_cultural",
    ):
        assert by_id[disabled].enabled is False, disabled
    for enabled in (
        "holaislascanarias",
        "cabildo_tenerife_deportes",
        "deportes_tenerife",
        "ticketmaster",
    ):
        assert by_id[enabled].enabled is True, enabled


def test_holaislascanarias_covers_all_islands_and_flags_license() -> None:
    hola = {s.id: s for s in load_sources(CONFIG)}["holaislascanarias"]
    assert set(hola.islands) == set(ISLANDS)
    assert hola.license is not None and hola.license.startswith("unknown")


def test_ticketmaster_reads_key_from_env_name_only() -> None:
    tm = {s.id: s for s in load_sources(CONFIG)}["ticketmaster"]
    assert tm.auth_env == "TICKETMASTER_API_KEY"
    assert "apikey" not in tm.url.lower()


def test_municipal_tribe_sources_name_their_municipality() -> None:
    by_id = {s.id: s for s in load_sources(CONFIG)}
    for source_id, municipality in {
        "candelaria": "Candelaria", "santiago_del_teide": "Santiago del Teide",
        "granadilla_de_abona": "Granadilla de Abona", "antigua": "Antigua",
        "los_silos": "Los Silos", "santa_ursula": "Santa Úrsula",
        "valle_gran_rey": "Valle Gran Rey",
    }.items():  # fmt: skip
        assert by_id[source_id].municipality == municipality
        assert by_id[source_id].adapter == "tribe_events"


def test_catalog_lists_candidates_and_excluded_with_check_date() -> None:
    catalog = load_catalog(CONFIG)
    assert catalog.candidates and catalog.excluded
    names = {e.name for e in (*catalog.candidates, *catalog.excluded)}
    assert {"Eventbrite", "Meetup", "webtenerife.com", "Telde"} <= names
    for entry in (*catalog.candidates, *catalog.excluded):
        assert entry.reason
        assert entry.checked_at is not None and entry.checked_at.isoformat() == "2026-10-15"


@pytest.mark.parametrize(
    "adapter", ["tribe_events", "drupal_jsonapi", "ckan", "ical", "rss", "ticketmaster"]
)
def test_all_adapters_registered(adapter: str) -> None:
    assert adapter in ADAPTERS


def test_priorities_and_stale_flags() -> None:
    by_id = {s.id: s for s in load_sources(CONFIG)}
    assert by_id["cabildo_tenerife_deportes"].priority > by_id["holaislascanarias"].priority
    assert by_id["holaislascanarias"].priority > by_id["ticketmaster"].priority
    assert {i for i, s in by_id.items() if s.stale} == {
        "gobcan_agenda_cultural",
        "santacruz_agenda_cultural",
    }


def test_discovery_probes_cover_the_candidate_domains() -> None:
    catalog = load_catalog(CONFIG)
    probes = set(catalog.discovery_probes)
    assert len(probes) == len(catalog.discovery_probes) >= 30
    for entry in catalog.candidates:
        if entry.url:
            host = urlsplit(entry.url).hostname
            assert host in probes or entry.name == "PredictHQ"


def test_catalog_ai_section_defaults_to_disabled_and_local_only(tmp_path: Path) -> None:
    plain = tmp_path / "a.yaml"
    plain.write_text("sources: []\n", encoding="utf-8")
    assert load_catalog(plain).ai.enabled is False
    on = tmp_path / "b.yaml"
    on.write_text("ai:\n  enabled: true\n  model: llama3.2:3b\nsources: []\n", encoding="utf-8")
    catalog = load_catalog(on)
    assert (catalog.ai.enabled, catalog.ai.model, catalog.ai.base_url) == (
        True, "llama3.2:3b", "http://127.0.0.1:11434"
    )  # fmt: skip
    remote = tmp_path / "c.yaml"
    remote.write_text("ai:\n  base_url: https://api.openai.com\nsources: []\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalog(remote)


WEB_SOURCES = {
    "webtenerife", "auditorio_teatro_lpa", "teatro_leal", "tea_tenerife", "adeje_agenda",
    "arona_agenda", "lpacultura", "cact_lanzarote", "cabildo_fuerteventura_agenda",
    "grancanaria_agenda", "tenerife_es_eventos", "auditorio_tenerife",
}  # fmt: skip


def test_html_sources_are_configured_below_apis_and_open_data() -> None:
    from canarias_events.sources.web import WebAdapter

    by_id = {s.id: s for s in load_sources(CONFIG)}
    assert WEB_SOURCES <= set(by_id)
    for source_id in WEB_SOURCES:
        source = by_id[source_id]
        assert source.adapter == "web"
        assert source.priority == 40
        assert source.priority < by_id["holaislascanarias"].priority
        assert source.notes
        WebAdapter(source)  # params validate


def test_html_sources_needing_a_renderer_are_disabled_by_default() -> None:
    by_id = {s.id: s for s in load_sources(CONFIG)}
    disabled = {i for i in WEB_SOURCES if not by_id[i].enabled}
    assert disabled == {"grancanaria_agenda", "tenerife_es_eventos", "auditorio_tenerife"}
    assert all(by_id[i].params["render"] in ("auto", "playwright") for i in disabled)
    # sources whose years are missing on the page say so explicitly (and are flagged in output)
    for source_id in ("adeje_agenda", "arona_agenda", "lpacultura", "cact_lanzarote"):
        assert by_id[source_id].params["year_policy"] == "infer"
    assert by_id["webtenerife"].params["sitemap_url_pattern"]


def test_blocked_and_unreliable_portals_are_documented_as_excluded() -> None:
    catalog = load_catalog(CONFIG)
    excluded = {e.name: e.reason for e in catalog.excluded}
    assert "never bypassed" in excluded["lanzarote.com"]
    assert "never bypassed" in excluded["Tomaticket"]
    assert "placeholder" in excluded["Turismo Lanzarote"]
    assert not {s.id for s in catalog.sources} & {"lanzarote_com", "tomaticket", "turismolanzarote"}
