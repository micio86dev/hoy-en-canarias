from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from hoy_api.db.models import Submission
from hoy_api.moderation.jev import Verdict
from hoy_api.moderation.redact import redact_personal_data
from hoy_api.moderation.rules import SubmissionFacts, check_rules
from hoy_api.publish.categories import map_category
from hoy_api.publish.persist import SUBMISSION_PREFIX, upsert_event
from hoy_api.publish.projection import ProjectedEvent, SourceLink, safe_http_url
from hoy_api.publish.slug import make_slug

CANARY_TZ_NAME = "Atlantic/Canary"
SOURCE_NAME = "Envío de visitante"


class SpamModel(Protocol):
    def spam_probability(self, text: str) -> Verdict: ...


@dataclass(frozen=True)
class Decision:
    status: str  # "approved" | "rejected" | "pending"
    reason: str | None


def decide(
    facts: SubmissionFacts,
    moderator: SpamModel,
    today: date,
    reject_above: float,
    approve_below: float,
) -> Decision:
    """Deterministic rules first. The model is consulted only when they pass, and never
    decides alone in the uncertain band: that case waits for a person."""
    reasons = check_rules(facts, today)
    if reasons:
        return Decision("rejected", reasons[0])
    text = redact_personal_data(f"{facts.title}\n{facts.description}")
    verdict = moderator.spam_probability(text)
    if verdict.probability is None:
        return Decision("pending", "no_verdict")
    if verdict.probability >= reject_above:
        return Decision("rejected", "spam_model")
    if verdict.probability <= approve_below:
        return Decision("approved", None)
    return Decision("pending", "uncertain")


def to_projected(submission_id: UUID, payload: dict[str, Any]) -> ProjectedEvent:
    importer_id = f"{SUBMISSION_PREFIX}{submission_id}"
    title = str(payload["title"]).strip()
    url = safe_http_url(payload.get("url"))
    start = date.fromisoformat(str(payload["start_date"]))
    end_raw = payload.get("end_date")
    end = date.fromisoformat(str(end_raw)) if end_raw else None
    description = str(payload.get("description") or "").strip() or None
    return ProjectedEvent(
        importer_id=importer_id,
        slug=make_slug(title, str(submission_id)),
        title=title,
        description=description,
        category=map_category(None, []),
        category_raw=None,
        tags=(),
        start_at=None,
        end_at=None,
        start_date=start,
        end_date=end,
        all_day=True,
        timezone=CANARY_TZ_NAME,
        venue_name=payload.get("venue") or None,
        address=None,
        municipality=payload.get("municipality") or None,
        island=payload.get("island") or None,
        latitude=None,
        longitude=None,
        price_min=None,
        price_max=None,
        currency=None,
        is_free=None,
        booking_url=None,
        ticket_url=None,
        official_event_url=url,
        image_url=None,
        organizer_name=None,
        organizer_url=None,
        status="unknown",
        first_seen_at=None,
        last_seen_at=None,
        last_verified_at=None,
        sources=(
            SourceLink(
                source_name=SOURCE_NAME,
                source_event_id=str(submission_id),
                source_url=None,
                official_url=url,
            ),
        ),
    )


def store(
    session: Session, payload: dict[str, Any], decision: Decision, now: datetime
) -> Submission:
    submission = Submission(
        status=decision.status,
        reason=decision.reason,
        payload=payload,
        decided_at=None if decision.status == "pending" else now,
    )
    session.add(submission)
    session.flush()
    if decision.status == "approved":
        upsert_event(session, to_projected(submission.id, payload), now)
    return submission


def approve(session: Session, submission: Submission, now: datetime) -> None:
    submission.status = "approved"
    submission.reason = None
    submission.decided_at = now
    upsert_event(session, to_projected(submission.id, submission.payload), now)


def reject(submission: Submission, reason: str, now: datetime) -> None:
    submission.status = "rejected"
    submission.reason = reason
    submission.decided_at = now
