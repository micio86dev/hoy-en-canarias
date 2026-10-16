from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from canarias_events.geo import ISLANDS
from hoy_api.db.models import Submission
from hoy_api.deps import ClockDep, SessionDep, enforce_rate_limit, require_admin
from hoy_api.moderation.rules import SubmissionFacts
from hoy_api.moderation.service import approve, decide, reject, store
from hoy_api.time_windows import local_today

public = APIRouter(
    prefix="/submissions", tags=["submissions"], dependencies=[Depends(enforce_rate_limit)]
)
admin = APIRouter(
    prefix="/admin/submissions", tags=["admin"], dependencies=[Depends(require_admin)]
)


class SubmissionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=2000)
    start_date: date
    end_date: date | None = None
    island: str
    municipality: str | None = Field(default=None, max_length=120)
    venue: str | None = Field(default=None, max_length=160)
    url: str | None = Field(default=None, max_length=500)
    honeypot: str = Field(default="", max_length=200)

    @field_validator("island")
    @classmethod
    def _known_island(cls, value: str) -> str:
        if value not in ISLANDS:
            raise ValueError("unknown island")
        return value


class SubmissionReceipt(BaseModel):
    id: UUID
    status: str


class SubmissionOut(BaseModel):
    id: UUID
    status: str
    reason: str | None
    title: str
    start_date: str
    island: str
    municipality: str | None
    url: str | None
    description: str


def _as_out(submission: Submission) -> SubmissionOut:
    payload: dict[str, Any] = submission.payload
    return SubmissionOut(
        id=submission.id,
        status=submission.status,
        reason=submission.reason,
        title=payload["title"],
        start_date=payload["start_date"],
        island=payload["island"],
        municipality=payload.get("municipality"),
        url=payload.get("url"),
        description=payload.get("description") or "",
    )


@public.post("", status_code=202, response_model=SubmissionReceipt)
def submit(
    body: SubmissionIn, request: Request, session: SessionDep, now: ClockDep
) -> SubmissionReceipt:
    """Accepts a visitor's event. The answer is always the same, so spammers learn nothing."""
    state = request.app.state
    facts = SubmissionFacts(
        title=body.title.strip(),
        description=body.description,
        start_date=body.start_date,
        url=body.url,
        honeypot=body.honeypot,
    )
    decision = decide(
        facts,
        state.moderator,
        local_today(now),
        reject_above=state.settings.moderation_reject_above,
    )
    payload = body.model_dump(exclude={"honeypot"}, mode="json")
    submission = store(session, payload, decision, now)
    session.commit()
    return SubmissionReceipt(id=submission.id, status="received")


class SubmissionList(BaseModel):
    items: list[SubmissionOut]


@admin.get("", response_model=SubmissionList)
def list_submissions(
    session: SessionDep,
    status: Annotated[str, Query(pattern="^(pending|approved|rejected)$")] = "pending",
) -> SubmissionList:
    rows = session.scalars(
        select(Submission)
        .where(Submission.status == status)
        .order_by(Submission.created_at)
        .limit(200)
    ).all()
    return SubmissionList(items=[_as_out(row) for row in rows])


def _pending(session: SessionDep, submission_id: UUID) -> Submission:
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    if submission.status != "pending":
        raise HTTPException(status_code=409, detail="Submission already decided")
    return submission


@admin.post("/{submission_id}/approve", response_model=SubmissionOut)
def approve_submission(submission_id: UUID, session: SessionDep, now: ClockDep) -> SubmissionOut:
    submission = _pending(session, submission_id)
    approve(session, submission, now)
    session.commit()
    return _as_out(submission)


@admin.post("/{submission_id}/reject", response_model=SubmissionOut)
def reject_submission(submission_id: UUID, session: SessionDep, now: ClockDep) -> SubmissionOut:
    submission = _pending(session, submission_id)
    reject(submission, "rejected_by_moderator", now)
    session.commit()
    return _as_out(submission)
