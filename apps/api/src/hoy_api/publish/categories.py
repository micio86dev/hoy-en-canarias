import re
import unicodedata
from collections.abc import Iterable

# Ordered rules: the first category whose keyword prefix appears in the raw category or
# tags wins. Order matters for overlaps (e.g. "Teatro infantil" -> family, not culture).
CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("nightlife", ("discoteca", "club", "nightlife", "noche", "after")),
    ("music", ("musica", "concierto", "concert", "jazz", "rock", "dj", "orquesta", "banda")),
    ("markets", ("mercado", "mercadillo", "market", "mercatino", "feria")),
    (
        "sports",
        ("deporte", "sport", "futbol", "running", "carrera", "maraton", "torneo", "partido"),
    ),
    (
        "family",
        ("infantil", "ninos", "familia", "familiar", "family", "kids", "bambini", "famiglia"),
    ),
    ("workshops", ("taller", "workshop", "curso", "formacion", "laboratorio", "corso")),
    (
        "nature",
        ("senderismo", "ruta", "naturaleza", "hiking", "excursion", "trail", "playa", "natura"),
    ),
    (
        "culture",
        (
            "teatro",
            "cine",
            "exposicion",
            "museo",
            "cultura",
            "conferencia",
            "charla",
            "libro",
            "danza",
            "arte",
            "theatre",
            "theater",
            "mostra",
            "culture",
            "literatura",
        ),
    ),
    ("festivals", ("fiesta", "festival", "carnaval", "romeria", "sagra", "feast")),
)

FALLBACK_CATEGORY = "other"


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()


def _match(text: str) -> str | None:
    for category, keywords in CATEGORY_RULES:
        pattern = "|".join(rf"\b{re.escape(keyword)}" for keyword in keywords)
        if re.search(pattern, text):
            return category
    return None


def map_category(raw: str | None, tags: Iterable[str]) -> str:
    """Map a source category onto the portal's fixed set.

    The source's own category is consulted before any tag, so an explicit "Taller" is not
    overridden by an unrelated tag such as "música".
    """
    for part in [raw or "", *tags]:
        if not part:
            continue
        category = _match(_fold(part))
        if category is not None:
            return category
    return FALLBACK_CATEGORY
