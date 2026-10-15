"""Static Canary Islands geography: 88 municipalities, 8 islands, coarse bounding boxes.

Nothing here guesses: unknown names and points outside every island box return None.
La Graciosa belongs administratively to Teguise (Lanzarote) but is a separate island.
"""

from __future__ import annotations

import math

from canarias_events.normalize import normalize_title

ISLANDS: tuple[str, ...] = (
    "Tenerife",
    "Gran Canaria",
    "Lanzarote",
    "Fuerteventura",
    "La Palma",
    "La Gomera",
    "El Hierro",
    "La Graciosa",
)

_BY_ISLAND: dict[str, tuple[str, ...]] = {
    "Tenerife": (
        "Adeje", "Arafo", "Arico", "Arona", "Buenavista del Norte", "Candelaria", "Fasnia",
        "Garachico", "Granadilla de Abona", "La Guancha", "Guía de Isora", "Güímar",
        "Icod de los Vinos", "La Matanza de Acentejo", "La Orotava", "Puerto de la Cruz",
        "Los Realejos", "El Rosario", "San Cristóbal de La Laguna", "San Juan de la Rambla",
        "San Miguel de Abona", "Santa Cruz de Tenerife", "Santa Úrsula", "Santiago del Teide",
        "El Sauzal", "Los Silos", "Tacoronte", "El Tanque", "Tegueste",
        "La Victoria de Acentejo", "Vilaflor de Chasna",
    ),
    "Gran Canaria": (
        "Agaete", "Agüimes", "Artenara", "Arucas", "Firgas", "Gáldar", "Ingenio", "Mogán",
        "Moya", "La Aldea de San Nicolás", "Las Palmas de Gran Canaria",
        "San Bartolomé de Tirajana", "Santa Brígida", "Santa Lucía de Tirajana",
        "Santa María de Guía de Gran Canaria", "Tejeda", "Telde", "Teror", "Valleseco",
        "Valsequillo de Gran Canaria", "Vega de San Mateo",
    ),
    "Lanzarote": (
        "Arrecife", "Haría", "San Bartolomé", "Teguise", "Tías", "Tinajo", "Yaiza",
    ),
    "Fuerteventura": (
        "Antigua", "Betancuria", "La Oliva", "Pájara", "Puerto del Rosario", "Tuineje",
    ),
    "La Palma": (
        "Barlovento", "Breña Alta", "Breña Baja", "Fuencaliente de la Palma", "Garafía",
        "Los Llanos de Aridane", "El Paso", "Puntagorda", "Puntallana", "San Andrés y Sauces",
        "Santa Cruz de La Palma", "Tazacorte", "Tijarafe", "Villa de Mazo",
    ),
    "La Gomera": (
        "Agulo", "Alajeró", "Hermigua", "San Sebastián de La Gomera", "Valle Gran Rey",
        "Vallehermoso",
    ),
    "El Hierro": ("Frontera", "Valverde", "El Pinar de El Hierro"),
}  # fmt: skip

# Canonical municipality name -> island.
MUNICIPALITIES: dict[str, str] = {
    name: island for island, names in _BY_ISLAND.items() for name in names
}

# Variant spellings -> canonical municipality name (accents/case are handled separately).
_ALIASES: dict[str, str] = {
    "La Laguna": "San Cristóbal de La Laguna",
    "Laguna": "San Cristóbal de La Laguna",
    "Las Palmas": "Las Palmas de Gran Canaria",
    "Las Palmas de GC": "Las Palmas de Gran Canaria",
    "Orotava": "La Orotava",
    "Realejos": "Los Realejos",
    "Rosario": "El Rosario",
    "Sauzal": "El Sauzal",
    "Tanque": "El Tanque",
    "Guancha": "La Guancha",
    "Matanza de Acentejo": "La Matanza de Acentejo",
    "Victoria de Acentejo": "La Victoria de Acentejo",
    "Vilaflor": "Vilaflor de Chasna",
    "Granadilla": "Granadilla de Abona",
    "Icod": "Icod de los Vinos",
    "Buenavista": "Buenavista del Norte",
    "Aldea de San Nicolás": "La Aldea de San Nicolás",
    "La Aldea": "La Aldea de San Nicolás",
    "Santa María de Guía": "Santa María de Guía de Gran Canaria",
    "Guía de Gran Canaria": "Santa María de Guía de Gran Canaria",
    "San Mateo": "Vega de San Mateo",
    "Valsequillo": "Valsequillo de Gran Canaria",
    "Fuencaliente": "Fuencaliente de la Palma",
    "Los Llanos": "Los Llanos de Aridane",
    "Santa Cruz de la Palma": "Santa Cruz de La Palma",
    "Mazo": "Villa de Mazo",
    "San Sebastián de la Gomera": "San Sebastián de La Gomera",
    "La Frontera": "Frontera",
    "El Pinar": "El Pinar de El Hierro",
    "Pinar de El Hierro": "El Pinar de El Hierro",
}

_MUNICIPALITY_KEYS: dict[str, str] = {
    normalize_title(name): name for name in (*MUNICIPALITIES, *_ALIASES)
}
_MUNICIPALITY_KEYS = {key: _ALIASES.get(name, name) for key, name in _MUNICIPALITY_KEYS.items()}

_ISLAND_KEYS: dict[str, str] = {
    **{normalize_title(i): i for i in ISLANDS},
    "gomera": "La Gomera",
    "hierro": "El Hierro",
    "graciosa": "La Graciosa",
}


def normalize_municipality(name: str | None) -> str | None:
    """Canonical municipality name for a (possibly variant) spelling, else None."""
    if not name or not name.strip():
        return None
    return _MUNICIPALITY_KEYS.get(normalize_title(name))


def island_for_municipality(name: str | None) -> str | None:
    canonical = normalize_municipality(name)
    return MUNICIPALITIES[canonical] if canonical else None


def normalize_island(name: str | None) -> str | None:
    """Canonical island name for 'Tenerife', 'Isla de Tenerife', 'gomera'..., else None."""
    if not name or not name.strip():
        return None
    key = normalize_title(name)
    for prefix in ("isla de ", "isla "):
        if key.startswith(prefix):
            key = key[len(prefix) :]
            break
    return _ISLAND_KEYS.get(key)


# (min_lat, max_lat, min_lon, max_lon). Coarse on purpose; La Graciosa is checked before
# Lanzarote because their boxes touch.
_BOXES: tuple[tuple[str, float, float, float, float], ...] = (
    ("La Graciosa", 29.215, 29.42, -13.62, -13.47),
    ("Lanzarote", 28.82, 29.25, -13.95, -13.40),
    ("Fuerteventura", 28.03, 28.80, -14.55, -13.80),
    ("Gran Canaria", 27.72, 28.19, -15.86, -15.35),
    ("Tenerife", 27.98, 28.60, -16.93, -16.10),
    ("La Gomera", 27.97, 28.22, -17.36, -17.08),
    ("La Palma", 28.40, 28.87, -18.02, -17.70),
    ("El Hierro", 27.60, 27.90, -18.18, -17.84),
)


def island_for_coordinates(lat: float | None, lon: float | None) -> str | None:
    """Coarse bounding-box lookup; None when the point is outside every island box."""
    if lat is None or lon is None or not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    for island, min_lat, max_lat, min_lon, max_lon in _BOXES:
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return island
    return None
