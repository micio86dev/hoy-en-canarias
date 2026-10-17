import hashlib
from uuid import UUID

from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from hoy_api.db.models import EventAttendance


def guest_hash(guest_id: UUID) -> str:
    """The stored form of a guest identifier. The raw identifier never reaches the database."""
    return hashlib.sha256(str(guest_id).encode("utf-8")).hexdigest()


def attendee_count(session: Session, event_id: UUID) -> int:
    count = session.scalar(
        select(func.count())
        .select_from(EventAttendance)
        .where(EventAttendance.event_id == event_id)
    )
    return int(count or 0)


def is_attending(session: Session, event_id: UUID, digest: str) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    EventAttendance.event_id == event_id, EventAttendance.guest_hash == digest
                )
            )
        )
    )


def join(session: Session, event_id: UUID, digest: str) -> None:
    session.execute(
        insert(EventAttendance)
        .values(event_id=event_id, guest_hash=digest)
        .on_conflict_do_nothing(constraint="event_attendances_event_guest_key")
    )


def leave(session: Session, event_id: UUID, digest: str) -> None:
    session.execute(
        delete(EventAttendance).where(
            EventAttendance.event_id == event_id, EventAttendance.guest_hash == digest
        )
    )
