"""Development and end-to-end fixtures. Never loaded by the production worker.

Dates are anchored to a given local day so the "today" and "upcoming" views stay meaningful
whenever the data is loaded.
"""

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from canarias_events.database import Database, Decision
from canarias_events.models import Event as ImportedEvent
from canarias_events.sources.base import SourceConfig

CANARY = ZoneInfo("Atlantic/Canary")


def _at(day: date, hour: int) -> datetime:
    """A local wall-clock time on ``day`` in the Canary Islands, as an aware instant."""
    return datetime.combine(day, time(hour, 0), tzinfo=CANARY).astimezone(UTC)


def _event(source: str, eid: str, title: str, **fields: object) -> tuple[str, ImportedEvent]:
    base: dict[str, object] = {
        "title": title,
        "source_name": f"Fuente {source}",
        "source_event_id": eid,
        "source_url": f"https://{source}.test/e/{eid}",
        "island": "Tenerife",
        "municipality": "Adeje",
        "category": "Conciertos",
        "tags": ["música"],
    }
    base.update(fields)
    return source, ImportedEvent.model_validate(base)


def demo_events(anchor: date) -> list[tuple[str, ImportedEvent]]:
    """Demo catalogue relative to ``anchor``: today, next days, free, paid, mapped and past."""
    return [
        _event(
            "a",
            "jazz",
            "Concierto de jazz en el puerto",
            start_datetime=_at(anchor + timedelta(days=5), 20),
            end_datetime=_at(anchor + timedelta(days=5), 22),
            description="<p>Jazz al atardecer.</p>",
            is_free=True,
            latitude=28.1,
            longitude=-16.7,
        ),
        _event(
            "a",
            "cana",
            "Fiesta de la Caña Dulce",
            start_datetime=_at(anchor + timedelta(days=1), 12),
            island="Gran Canaria",
            municipality="Telde",
            category="Fiesta",
            tags=["fiesta"],
            is_free=False,
            price_min=5.0,
            currency="EUR",
            latitude=27.9,
            longitude=-15.4,
        ),
        _event(
            "b",
            "mercado",
            "Mercado de artesanía",
            start_datetime=_at(anchor, 10),
            island="Lanzarote",
            municipality="Teguise",
            category="Mercado",
            tags=[],
        ),
        _event(
            "b",
            "rock",
            "Concierto de rock en Adeje",
            start_datetime=_at(anchor + timedelta(days=9), 21),
        ),
        _event(
            "b",
            "pasado",
            "Festival del año pasado",
            start_datetime=_at(anchor - timedelta(days=10), 12),
            island="La Palma",
            municipality="Los Llanos de Aridane",
            category="Fiesta",
        ),
    ]


def write_importer_database(path: Path, anchor: date, now: datetime) -> None:
    """Build an importer SQLite database with the demo catalogue, as the importer itself would."""
    with Database(path) as db:
        db.sync_sources(
            [
                SourceConfig(id="a", name="Fuente a", adapter="web", url="https://a.test/"),
                SourceConfig(id="b", name="Fuente b", adapter="web", url="https://b.test/"),
            ]
        )
        for source_id, event in demo_events(anchor):
            db.upsert(event, source_id, Decision("new"), now)
