from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlsplit

MAX_TITLE = 160
MAX_DESCRIPTION = 2000
MAX_AHEAD = timedelta(days=366)


@dataclass(frozen=True)
class SubmissionFacts:
    title: str
    description: str
    start_date: date
    url: str | None
    honeypot: str


def _is_http_url(value: str) -> bool:
    parts = urlsplit(value)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def check_rules(facts: SubmissionFacts, today: date) -> list[str]:
    """Cheap, free checks. Any reason here rejects the submission before the model is called."""
    reasons: list[str] = []
    if facts.honeypot.strip():
        reasons.append("honeypot")
    if facts.url is not None and not _is_http_url(facts.url):
        reasons.append("url")
    if facts.start_date < today:
        reasons.append("date_in_past")
    if facts.start_date > today + MAX_AHEAD:
        reasons.append("date_too_far")
    if not 3 <= len(facts.title.strip()) <= MAX_TITLE:
        reasons.append("title")
    if len(facts.description) > MAX_DESCRIPTION:
        reasons.append("description")
    return reasons
