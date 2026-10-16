"""Derived map points for events: source coordinates, known venues, municipality reference points.

``Event.latitude``/``Event.longitude`` stay exactly what the source gave. A derived point lives
in a separate :class:`MapPoint` with an explicit precision and the basis it came from. Nothing
here guesses: no fuzzy venue matching, no title parsing, no island-only points.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from canarias_events.geo import (
    MUNICIPALITIES,
    island_for_coordinates,
    island_for_municipality,
    normalize_island,
    normalize_municipality,
)
from canarias_events.models import Event
from canarias_events.normalize import normalize_title

Precision = Literal["source", "venue", "municipality"]

# Canonical municipality name -> (latitude, longitude, Wikidata item).
# Source: Wikidata P625 (coordinate location, usually the main town), CC0, fetched 2026-10-16.
# These are approximate reference points, not event locations.
MUNICIPALITY_POINTS: dict[str, tuple[float, float, str]] = {
    "Adeje": (28.11667, -16.71667, "Q353618"),
    "Agaete": (28.10169, -15.70074, "Q390069"),
    "Agulo": (28.18823, -17.19464, "Q398588"),
    "Agüimes": (27.88333, -15.43333, "Q399702"),
    "Alajeró": (28.06199, -17.23837, "Q932914"),
    "Antigua": (28.41602, -14.01185, "Q575670"),
    "Arafo": (28.34045, -16.41733, "Q624967"),
    "Arico": (28.19042, -16.49772, "Q657738"),
    "Arona": (28.09962, -16.68091, "Q698246"),
    "Arrecife": (28.9625, -13.55056, "Q647543"),
    "Artenara": (28.02022, -15.64573, "Q706892"),
    "Arucas": (28.11845, -15.52322, "Q716384"),
    "Barlovento": (28.8272, -17.80381, "Q433918"),
    "Betancuria": (28.42472, -14.05722, "Q830969"),
    "Breña Alta": (28.66152, -17.7851, "Q433958"),
    "Breña Baja": (28.64432, -17.77329, "Q433986"),
    "Buenavista del Norte": (28.37206, -16.85143, "Q594961"),
    "Candelaria": (28.35467, -16.37101, "Q487268"),
    "El Paso": (28.65129, -17.88057, "Q434123"),
    "El Pinar de El Hierro": (27.70139, -17.98, "Q1324548"),
    "El Rosario": (28.4328, -16.36821, "Q176624"),
    "El Sauzal": (28.47988, -16.43575, "Q782910"),
    "El Tanque": (28.35627, -16.77981, "Q770882"),
    "Fasnia": (28.24006, -16.44, "Q524431"),
    "Firgas": (28.1, -15.55, "Q606753"),
    "Frontera": (27.75348, -18.01076, "Q1798827"),
    "Fuencaliente de la Palma": (28.49345, -17.84499, "Q434019"),
    "Garachico": (28.37369, -16.76405, "Q612815"),
    "Garafía": (28.80416, -17.91672, "Q434053"),
    "Granadilla de Abona": (28.11667, -16.58333, "Q943431"),
    "Guía de Isora": (28.21104, -16.7784, "Q765879"),
    "Gáldar": (28.14389, -15.65028, "Q956757"),
    "Güímar": (28.31502, -16.40995, "Q983433"),
    "Haría": (29.14681, -13.49819, "Q1588135"),
    "Hermigua": (28.16296, -17.19849, "Q1260011"),
    "Icod de los Vinos": (28.35, -16.7, "Q572542"),
    "Ingenio": (27.92141, -15.43236, "Q985599"),
    "La Aldea de San Nicolás": (27.9826, -15.77991, "Q986727"),
    "La Guancha": (28.37383, -16.65158, "Q983556"),
    "La Matanza de Acentejo": (28.44028, -16.43889, "Q983577"),
    "La Oliva": (28.61667, -13.93333, "Q594892"),
    "La Orotava": (28.36667, -16.51667, "Q920391"),
    "La Victoria de Acentejo": (28.43479, -16.46817, "Q985601"),
    "Las Palmas de Gran Canaria": (28.12722, -15.43139, "Q11974"),
    "Los Llanos de Aridane": (28.65823, -17.91423, "Q366948"),
    "Los Realejos": (28.35, -16.6, "Q953876"),
    "Los Silos": (28.36582, -16.81671, "Q682964"),
    "Mogán": (27.86667, -15.71667, "Q628446"),
    "Moya": (28.11077, -15.58322, "Q985598"),
    "Puerto de la Cruz": (28.41667, -16.55, "Q623549"),
    "Puerto del Rosario": (28.5, -13.86667, "Q540185"),
    "Puntagorda": (28.77331, -17.98818, "Q434150"),
    "Puntallana": (28.73884, -17.74531, "Q434173"),
    "Pájara": (28.35, -14.1, "Q661198"),
    "San Andrés y Sauces": (28.80447, -17.77417, "Q434192"),
    "San Bartolomé": (29.00163, -13.61393, "Q962773"),
    "San Bartolomé de Tirajana": (27.92543, -15.57258, "Q693258"),
    "San Cristóbal de La Laguna": (28.48528, -16.31667, "Q54898"),
    "San Juan de la Rambla": (28.3931, -16.64917, "Q986364"),
    "San Miguel de Abona": (28.0975, -16.61722, "Q677038"),
    "San Sebastián de La Gomera": (28.09222, -17.11, "Q512009"),
    "Santa Brígida": (28.0338, -15.49984, "Q984488"),
    "Santa Cruz de La Palma": (28.6825, -17.765, "Q433632"),
    "Santa Cruz de Tenerife": (28.46667, -16.25, "Q14328"),
    "Santa Lucía de Tirajana": (27.91209, -15.54069, "Q738674"),
    "Santa María de Guía de Gran Canaria": (28.13889, -15.63278, "Q986359"),
    "Santa Úrsula": (28.42529, -16.4917, "Q657800"),
    "Santiago del Teide": (28.29574, -16.81448, "Q915283"),
    "Tacoronte": (28.48053, -16.41376, "Q944264"),
    "Tazacorte": (28.64248, -17.93178, "Q426350"),
    "Tegueste": (28.52326, -16.34076, "Q985603"),
    "Teguise": (29.06111, -13.55972, "Q737440"),
    "Tejeda": (27.99744, -15.61394, "Q986361"),
    "Telde": (27.99845, -15.41668, "Q55845"),
    "Teror": (28.059, -15.54757, "Q430147"),
    "Tijarafe": (28.71295, -17.95683, "Q434286"),
    "Tinajo": (29.05, -13.65, "Q638013"),
    "Tuineje": (28.31667, -14.05, "Q985586"),
    "Tías": (28.95306, -13.65306, "Q2244278"),
    "Valle Gran Rey": (28.11667, -17.31667, "Q584446"),
    "Vallehermoso": (28.17987, -17.26468, "Q635308"),
    "Valleseco": (28.04992, -15.57495, "Q986357"),
    "Valsequillo de Gran Canaria": (27.98084, -15.49892, "Q914195"),
    "Valverde": (27.80969, -17.91515, "Q1853429"),
    "Vega de San Mateo": (28.01056, -15.5325, "Q986353"),
    "Vilaflor de Chasna": (28.1591, -16.6365, "Q985693"),
    "Villa de Mazo": (28.60494, -17.77705, "Q434321"),
    "Yaiza": (28.955, -13.76667, "Q2323549"),
}


@dataclass(frozen=True)
class MapPoint:
    """A point to draw for an event, with how precise it is and where it came from."""

    latitude: float
    longitude: float
    precision: Precision
    basis: str


class Venue(BaseModel):
    """One entry of ``config/venues.yaml``: a named place with verified coordinates."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    municipality: str
    island: str
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    source: str = Field(min_length=1)  # provenance, e.g. "wikidata:Q950052"

    @model_validator(mode="after")
    def _check_location(self) -> Self:
        municipality = normalize_municipality(self.municipality)
        if municipality is None:
            raise ValueError(f"{self.name}: unknown municipality '{self.municipality}'")
        island = normalize_island(self.island)
        if island is None:
            raise ValueError(f"{self.name}: unknown island '{self.island}'")
        if MUNICIPALITIES[municipality] != island:
            raise ValueError(f"{self.name}: municipality {municipality} is not on island {island}")
        if island_for_coordinates(self.latitude, self.longitude) != island:
            raise ValueError(
                f"{self.name}: coordinates are not inside the declared island {island}"
            )
        self.municipality = municipality
        self.island = island
        return self

    def keys(self) -> set[str]:
        return {normalize_title(n) for n in (self.name, *self.aliases)}


def load_venues(path: Path | str) -> list[Venue]:
    """The venues in ``path``; a missing file is an empty table."""
    file = Path(path)
    if not file.exists():
        return []
    raw: Any = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    venues = [Venue.model_validate(item) for item in raw.get("venues") or []]
    seen: set[str] = set()
    for venue in venues:
        clash = seen & venue.keys()
        if clash:
            raise ValueError(f"duplicate venue name or alias '{sorted(clash)[0]}'")
        seen |= venue.keys()
    return venues


def _venue_point(event: Event, venues: list[Venue]) -> MapPoint | None:
    if not event.venue_name:
        return None
    key = normalize_title(event.venue_name)
    for venue in venues:
        if key not in venue.keys():
            continue
        if event.municipality and normalize_municipality(event.municipality) != venue.municipality:
            return None
        return MapPoint(venue.latitude, venue.longitude, "venue", f"{venue.name} ({venue.source})")
    return None


def _municipality_point(event: Event) -> MapPoint | None:
    municipality = normalize_municipality(event.municipality)
    if municipality is None or municipality not in MUNICIPALITY_POINTS:
        return None
    if event.island and normalize_island(event.island) != island_for_municipality(municipality):
        return None
    lat, lon, qid = MUNICIPALITY_POINTS[municipality]
    return MapPoint(lat, lon, "municipality", f"{municipality} (wikidata:{qid})")


def locate(event: Event, venues: list[Venue]) -> MapPoint | None:
    """The best honest point for ``event``, or None. Never alters the event."""
    if event.latitude is not None and event.longitude is not None:
        return MapPoint(event.latitude, event.longitude, "source", "event coordinates")
    return _venue_point(event, venues) or _municipality_point(event)
