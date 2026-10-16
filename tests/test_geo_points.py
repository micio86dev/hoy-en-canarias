from __future__ import annotations

from pathlib import Path

import pytest

from canarias_events.geo import MUNICIPALITIES, island_for_coordinates
from canarias_events.geo_points import (
    MUNICIPALITY_POINTS,
    Venue,
    load_venues,
    locate,
)
from canarias_events.models import Event

LEAL = Venue(
    name="Teatro Leal",
    aliases=[],
    municipality="San Cristóbal de La Laguna",
    island="Tenerife",
    latitude=28.48972,
    longitude=-16.31806,
    source="wikidata:Q950052",
)
TEA = Venue(
    name="TEA Tenerife Espacio de las Artes",
    aliases=["TEA", "Tenerife Espacio de las Artes"],
    municipality="Santa Cruz de Tenerife",
    island="Tenerife",
    latitude=28.46417,
    longitude=-16.25139,
    source="wikidata:Q2403459",
)
VENUES = [LEAL, TEA]


def make(**kwargs: object) -> Event:
    fields: dict[str, object] = {
        "title": "Concierto",
        "start_date": "2026-10-20",
        "source_name": "Src",
        "source_event_id": "1",
    }
    fields.update(kwargs)
    return Event.model_validate(fields)


def test_source_coordinates_win_and_are_not_modified() -> None:
    event = make(latitude=28.1, longitude=-16.5, venue_name="Teatro Leal")
    point = locate(event, VENUES)
    assert point is not None
    assert (point.latitude, point.longitude, point.precision) == (28.1, -16.5, "source")
    assert point.basis == "event coordinates"
    assert (event.latitude, event.longitude) == (28.1, -16.5)


def test_only_one_source_coordinate_is_not_a_source_point() -> None:
    event = make(latitude=28.1, municipality="Adeje")
    point = locate(event, VENUES)
    assert point is not None and point.precision == "municipality"
    assert event.latitude == 28.1 and event.longitude is None


def test_venue_exact_match_is_accent_and_case_insensitive() -> None:
    event = make(venue_name="  TEATRO   leal ", municipality="La Laguna")
    point = locate(event, VENUES)
    assert point is not None
    assert (point.latitude, point.longitude, point.precision) == (28.48972, -16.31806, "venue")
    assert "Teatro Leal" in point.basis and "wikidata:Q950052" in point.basis
    assert event.latitude is None and event.longitude is None


def test_venue_alias_matches() -> None:
    point = locate(make(venue_name="Tenerife Espacio de las Artes"), VENUES)
    assert point is not None and point.precision == "venue" and point.latitude == 28.46417
    point = locate(make(venue_name="tea"), VENUES)
    assert point is not None and point.precision == "venue"


def test_venue_with_conflicting_municipality_is_not_matched() -> None:
    event = make(venue_name="Teatro Leal", municipality="Adeje")
    point = locate(event, VENUES)
    assert point is not None and point.precision == "municipality"
    assert point.basis.startswith("Adeje")


def test_venue_without_municipality_matches() -> None:
    point = locate(make(venue_name="Teatro Leal"), VENUES)
    assert point is not None and point.precision == "venue"


def test_no_fuzzy_venue_matching() -> None:
    assert locate(make(venue_name="Teatro Leal Norte"), VENUES) is None
    assert locate(make(venue_name="Plaza del Teatro"), VENUES) is None


def test_municipality_fallback() -> None:
    point = locate(make(municipality="Adeje", island="Tenerife"), [])
    assert point is not None and point.precision == "municipality"
    lat, lon, qid = MUNICIPALITY_POINTS["Adeje"]
    assert (point.latitude, point.longitude) == (lat, lon)
    assert "Adeje" in point.basis and qid in point.basis


def test_municipality_island_mismatch_gives_none() -> None:
    assert locate(make(municipality="Adeje", island="Gran Canaria"), []) is None


def test_nothing_known_gives_none() -> None:
    assert locate(make(), VENUES) is None
    assert locate(make(island="Tenerife"), VENUES) is None
    assert locate(make(title="Fiesta en Adeje"), VENUES) is None
    assert locate(make(municipality="Atlantis"), VENUES) is None


def test_municipality_points_cover_exactly_the_known_municipalities() -> None:
    assert set(MUNICIPALITY_POINTS) == set(MUNICIPALITIES)


@pytest.mark.parametrize("name", sorted(MUNICIPALITIES))
def test_every_municipality_point_is_inside_its_island(name: str) -> None:
    lat, lon, qid = MUNICIPALITY_POINTS[name]
    assert island_for_coordinates(lat, lon) == MUNICIPALITIES[name]
    assert qid.startswith("Q")


# --------------------------------------------------------------------------- venues.yaml


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "venues.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_venues_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_venues(tmp_path / "nope.yaml") == []


def test_load_venues_reads_entries(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
venues:
  - name: Teatro Leal
    municipality: La Laguna
    island: Tenerife
    latitude: 28.48972
    longitude: -16.31806
    source: wikidata:Q950052
""",
    )
    (venue,) = load_venues(path)
    assert venue.municipality == "San Cristóbal de La Laguna"
    assert venue.aliases == []


def test_load_venues_rejects_coordinates_outside_declared_island(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
venues:
  - name: Teatro Leal
    municipality: La Laguna
    island: Gran Canaria
    latitude: 28.48972
    longitude: -16.31806
    source: wikidata:Q950052
""",
    )
    with pytest.raises(ValueError, match="island"):
        load_venues(path)


def test_load_venues_rejects_unknown_municipality(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
venues:
  - name: X
    municipality: Atlantis
    island: Tenerife
    latitude: 28.4
    longitude: -16.3
    source: manual
""",
    )
    with pytest.raises(ValueError, match="municipality"):
        load_venues(path)


def test_load_venues_rejects_duplicate_names_and_unknown_keys(tmp_path: Path) -> None:
    entry = """
  - name: Teatro Leal
    municipality: La Laguna
    island: Tenerife
    latitude: 28.48972
    longitude: -16.31806
    source: wikidata:Q950052
"""
    with pytest.raises(ValueError, match="duplicate"):
        load_venues(write(tmp_path, "venues:" + entry + entry))
    with pytest.raises(ValueError):
        load_venues(write(tmp_path, "venues:" + entry + "    colour: red\n"))


def test_bundled_venues_file_is_valid() -> None:
    venues = load_venues(Path("config/venues.yaml"))
    assert {v.name for v in venues} >= {"Teatro Leal", "Palmetum de Santa Cruz de Tenerife"}
