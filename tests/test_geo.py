from __future__ import annotations

from collections import Counter

import pytest

from canarias_events.geo import (
    ISLANDS,
    MUNICIPALITIES,
    island_for_coordinates,
    island_for_municipality,
    normalize_island,
    normalize_municipality,
)

EXPECTED_COUNTS = {
    "Tenerife": 31,
    "Gran Canaria": 21,
    "Lanzarote": 7,
    "Fuerteventura": 6,
    "La Palma": 14,
    "La Gomera": 6,
    "El Hierro": 3,
}


def test_total_is_88_municipalities() -> None:
    assert len(MUNICIPALITIES) == 88


def test_counts_per_island() -> None:
    assert dict(Counter(MUNICIPALITIES.values())) == EXPECTED_COUNTS


def test_islands_list_includes_la_graciosa() -> None:
    assert len(ISLANDS) == 8
    assert set(ISLANDS) == {*EXPECTED_COUNTS, "La Graciosa"}


def test_every_municipality_maps_to_a_known_island() -> None:
    assert set(MUNICIPALITIES.values()) <= set(ISLANDS)


@pytest.mark.parametrize(
    ("name", "island"),
    [
        ("Santa Cruz de Tenerife", "Tenerife"),
        ("San Cristóbal de La Laguna", "Tenerife"),
        ("La Laguna", "Tenerife"),
        ("san cristobal de la laguna", "Tenerife"),
        ("Güímar", "Tenerife"),
        ("guimar", "Tenerife"),
        ("Guía de Isora", "Tenerife"),
        ("Orotava", "Tenerife"),
        ("Las Palmas de Gran Canaria", "Gran Canaria"),
        ("LAS PALMAS", "Gran Canaria"),
        ("Agüimes", "Gran Canaria"),
        ("La Aldea de San Nicolás", "Gran Canaria"),
        ("Santa María de Guía de Gran Canaria", "Gran Canaria"),
        ("Arrecife", "Lanzarote"),
        ("Teguise", "Lanzarote"),
        ("Puerto del Rosario", "Fuerteventura"),
        ("Pájara", "Fuerteventura"),
        ("Santa Cruz de La Palma", "La Palma"),
        ("Los Llanos de Aridane", "La Palma"),
        ("San Sebastián de La Gomera", "La Gomera"),
        ("Valle Gran Rey", "La Gomera"),
        ("Valverde", "El Hierro"),
        ("Frontera", "El Hierro"),
        ("El Pinar de El Hierro", "El Hierro"),
        ("El Pinar", "El Hierro"),
        ("  Adeje  ", "Tenerife"),
    ],
)
def test_island_for_municipality(name: str, island: str) -> None:
    assert island_for_municipality(name) == island


@pytest.mark.parametrize("name", [None, "", "  ", "Madrid", "Santa Cruz", "Atlantis"])
def test_unknown_municipality_is_none(name: str | None) -> None:
    assert island_for_municipality(name) is None


def test_normalize_municipality_returns_canonical_name() -> None:
    assert normalize_municipality("la laguna") == "San Cristóbal de La Laguna"
    assert normalize_municipality("Las Palmas") == "Las Palmas de Gran Canaria"
    assert normalize_municipality("nowhere") is None


def test_frontera_alias_used_by_hola_islas_canarias() -> None:
    assert normalize_municipality("La Frontera") == "Frontera"
    assert island_for_municipality("La Frontera") == "El Hierro"


def test_la_graciosa_is_part_of_teguise() -> None:
    assert island_for_municipality("Teguise") == "Lanzarote"


@pytest.mark.parametrize(
    ("raw", "island"),
    [
        ("Tenerife", "Tenerife"),
        ("Isla de Tenerife", "Tenerife"),
        ("isla de tenerife", "Tenerife"),
        ("Gran Canaria", "Gran Canaria"),
        ("Isla de Gran Canaria", "Gran Canaria"),
        ("lanzarote", "Lanzarote"),
        ("Fuerteventura", "Fuerteventura"),
        ("La Palma", "La Palma"),
        ("Isla de La Palma", "La Palma"),
        ("La Gomera", "La Gomera"),
        ("Gomera", "La Gomera"),
        ("El Hierro", "El Hierro"),
        ("Hierro", "El Hierro"),
        ("La Graciosa", "La Graciosa"),
        ("Isla de La Graciosa", "La Graciosa"),
        ("Graciosa", "La Graciosa"),
    ],
)
def test_normalize_island(raw: str, island: str) -> None:
    assert normalize_island(raw) == island


@pytest.mark.parametrize("raw", [None, "", "Mallorca", "Canarias", "Palma"])
def test_normalize_island_unknown(raw: str | None) -> None:
    assert normalize_island(raw) is None


@pytest.mark.parametrize(
    ("lat", "lon", "island"),
    [
        (28.46, -16.25, "Tenerife"),  # Santa Cruz de Tenerife
        (28.12, -15.43, "Gran Canaria"),  # Las Palmas
        (29.23, -13.50, "La Graciosa"),  # Caleta de Sebo
        (28.96, -13.55, "Lanzarote"),  # Arrecife
        (28.50, -13.86, "Fuerteventura"),  # Puerto del Rosario
        (28.68, -17.76, "La Palma"),  # Santa Cruz de La Palma
        (28.09, -17.11, "La Gomera"),  # San Sebastian de La Gomera
        (27.81, -17.91, "El Hierro"),  # Valverde
    ],
)
def test_island_for_coordinates(lat: float, lon: float, island: str) -> None:
    assert island_for_coordinates(lat, lon) == island


@pytest.mark.parametrize(
    ("lat", "lon"),
    [(25.0, -20.0), (28.3, -16.0), (40.4, -3.7), (0.0, 0.0), (28.0, -15.0)],
)
def test_coordinates_outside_islands_are_none(lat: float, lon: float) -> None:
    assert island_for_coordinates(lat, lon) is None


def test_coordinates_invalid_inputs() -> None:
    assert island_for_coordinates(None, -16.25) is None
    assert island_for_coordinates(28.46, None) is None
    assert island_for_coordinates(float("nan"), -16.25) is None


def test_municipalities_named_after_en() -> None:
    from canarias_events.geo import municipalities_after_en

    assert municipalities_after_en("Concierto en Adeje el sábado") == {"Adeje"}
    assert municipalities_after_en("Fiesta EN LA LAGUNA y en Candelaria") == {
        "San Cristóbal de La Laguna",
        "Candelaria",
    }
    assert municipalities_after_en("Misa a la Virgen de Candelaria") == set()  # a name, not a place
    assert municipalities_after_en("Entradas en Adejeros") == set()  # whole words only
    assert municipalities_after_en("") == set()
    assert municipalities_after_en("Fiesta en San Bartolomé de Tirajana el sábado") == {
        "San Bartolomé de Tirajana"
    }  # the longest name wins over the shorter one it contains
