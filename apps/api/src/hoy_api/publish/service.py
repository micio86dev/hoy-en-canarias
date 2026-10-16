from datetime import datetime
from pathlib import Path

from hoy_api.publish.persist import PublishReport, publish_events
from hoy_api.publish.projection import ProjectedEvent, SourceLink, project_event, safe_http_url
from sqlalchemy.orm import Session, sessionmaker

from canarias_events.database import Database

__all__ = ["PublishReport", "publish_from_sqlite", "read_importer"]


def read_importer(sqlite_path: Path) -> tuple[list[ProjectedEvent], int]:
    """Read every stored importer event and project it. Returns (events, skipped_count).

    Events without an id or without any date cannot satisfy the schema and are skipped.
    """
    projected: list[ProjectedEvent] = []
    skipped = 0
    with Database(sqlite_path) as db:
        for event in db.all_events():
            if event.id is None or (event.start_datetime is None and event.start_date is None):
                skipped += 1
                continue
            links = [
                SourceLink(
                    source_name=row["source_name"],
                    source_event_id=row["source_event_id"],
                    source_url=safe_http_url(row["source_url"]),
                    official_url=safe_http_url(
                        row["official_event_url"] or row["canonical_official_url"]
                    ),
                )
                for row in db.event_sources(event.id)
            ]
            projected.append(project_event(event, links))
    return projected, skipped


def publish_from_sqlite(
    sqlite_path: Path, session_factory: sessionmaker[Session], now: datetime
) -> PublishReport:
    """Project the importer database into Postgres in a single transaction."""
    events, skipped = read_importer(sqlite_path)
    with session_factory.begin() as session:
        report = publish_events(session, events, now)
    report.skipped = skipped
    return report
