from datetime import UTC, datetime
from pathlib import Path

from canarias_events.database import Database, Decision
from canarias_events.models import Event as ImportedEvent
from canarias_events.sources.base import SourceConfig

# Friday 16 October 2026, 10:00 UTC: the fixed "now" for deterministic date windows.
FIXED_NOW = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)


def imported(source_id: str, eid: str, title: str, **overrides: object) -> ImportedEvent:
    base: dict[str, object] = {
        "title": title,
        "source_name": f"Fuente {source_id}",
        "source_event_id": eid,
        "source_url": f"https://{source_id}.test/e/{eid}",
        "start_datetime": datetime(2026, 10, 21, 20, 0, tzinfo=UTC),
        "island": "Tenerife",
        "municipality": "Adeje",
        "category": "Conciertos",
        "tags": ["música"],
    }
    base.update(overrides)
    return ImportedEvent.model_validate(base)


def seed_importer(path: Path, events: list[tuple[str, ImportedEvent]]) -> None:
    with Database(path) as db:
        db.sync_sources(
            [
                SourceConfig(id="a", name="Fuente a", adapter="web", url="https://a.test/"),
                SourceConfig(id="b", name="Fuente b", adapter="web", url="https://b.test/"),
            ]
        )
        for source_id, event in events:
            db.upsert(event, source_id, Decision("new"), FIXED_NOW)


def demo_events() -> list[tuple[str, ImportedEvent]]:
    """A small catalogue covering islands, categories, dates, prices and coordinates."""
    return [
        (
            "a",
            imported(
                "a",
                "1",
                "Concierto de jazz en el puerto",
                start_datetime=datetime(2026, 10, 21, 20, 0, tzinfo=UTC),
                end_datetime=datetime(2026, 10, 21, 22, 0, tzinfo=UTC),
                description="<p>Jazz al atardecer.</p>",
                is_free=True,
                latitude=28.1,
                longitude=-16.7,
            ),
        ),
        (
            "a",
            imported(
                "a",
                "2",
                "Fiesta de la Caña Dulce",
                start_datetime=datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
                island="Gran Canaria",
                municipality="Telde",
                category="Fiesta",
                tags=["fiesta"],
                is_free=False,
                price_min=5.0,
                latitude=27.9,
                longitude=-15.4,
            ),
        ),
        (
            "b",
            imported(
                "b",
                "3",
                "Mercado de artesanía",
                start_datetime=datetime(2026, 10, 19, 10, 0, tzinfo=UTC),
                island="Lanzarote",
                municipality="Teguise",
                category="Mercado",
                tags=[],
            ),
        ),
        (
            "b",
            imported(
                "b",
                "4",
                "Concierto de rock en Adeje",
                start_datetime=datetime(2026, 10, 23, 21, 0, tzinfo=UTC),
            ),
        ),
        (
            "b",
            imported(
                "b",
                "5",
                "Pasado festival",
                start_datetime=datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
            ),
        ),
    ]
