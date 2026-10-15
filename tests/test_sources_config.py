from __future__ import annotations

from pathlib import Path

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
