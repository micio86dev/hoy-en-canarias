from datetime import date

import pytest
from hoy_api.moderation.jev import Verdict, read_verdict
from hoy_api.moderation.redact import redact_personal_data
from hoy_api.moderation.rules import SubmissionFacts, check_rules

TODAY = date(2026, 10, 16)


def test_redacts_emails_and_phone_numbers() -> None:
    text = "Info: ana.lopez@example.com o +34 922 123 456 o 611-223-344"

    cleaned = redact_personal_data(text)

    assert "example.com" not in cleaned
    assert "922 123 456" not in cleaned
    assert "611-223-344" not in cleaned
    assert "Info:" in cleaned


def test_keeps_event_text_without_personal_data_unchanged() -> None:
    assert (
        redact_personal_data("Concierto de jazz en el puerto") == "Concierto de jazz en el puerto"
    )


def facts(**overrides: object) -> SubmissionFacts:
    base: dict[str, object] = {
        "title": "Concierto de jazz",
        "description": "Jazz al atardecer.",
        "start_date": date(2026, 10, 21),
        "url": "https://club.test/e/1",
        "honeypot": "",
    }
    base.update(overrides)
    return SubmissionFacts(**base)  # type: ignore[arg-type]


def test_clean_submission_passes_the_deterministic_rules() -> None:
    assert check_rules(facts(), today=TODAY) == []


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"honeypot": "https://spam.test"}, "honeypot"),
        ({"url": "javascript:alert(1)"}, "url"),
        ({"start_date": date(2025, 1, 1)}, "date_in_past"),
        ({"start_date": date(2029, 1, 1)}, "date_too_far"),
        ({"title": "x"}, "title"),
    ],
)
def test_rules_reject_obvious_problems_without_calling_the_model(
    overrides: dict[str, object], reason: str
) -> None:
    assert reason in check_rules(facts(**overrides), today=TODAY)


def test_verdict_reads_probability_when_the_answer_is_well_formed() -> None:
    verdict = read_verdict({"answers": {"q": {"probability": 0.97}}})

    assert verdict.probability == pytest.approx(0.97)
    assert verdict.usable is True


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"answers": {}},
        {"answers": {"q": {"probability": "high"}}},
        {"answers": {"q": {"probability": 1.7}}},
    ],
)
def test_malformed_or_missing_answers_are_unknown_never_guessed(payload: dict[str, object]) -> None:
    verdict = read_verdict(payload)

    assert verdict.probability is None
    assert verdict.usable is False


class _Model:
    def __init__(self, probability: float | None) -> None:
        self.probability = probability

    def spam_probability(self, text: str) -> Verdict:
        return Verdict(self.probability)


def test_automatic_decision_publishes_below_the_threshold() -> None:
    from hoy_api.moderation.service import decide

    assert decide(facts(), _Model(0.3), TODAY, reject_above=0.5).status == "approved"


def test_automatic_decision_rejects_at_and_above_the_threshold() -> None:
    from hoy_api.moderation.service import decide

    assert decide(facts(), _Model(0.5), TODAY, reject_above=0.5).status == "rejected"
    assert decide(facts(), _Model(0.9), TODAY, reject_above=0.5).reason == "spam_model"


def test_without_a_verdict_the_submission_waits_as_a_safe_default() -> None:
    from hoy_api.moderation.service import decide

    assert decide(facts(), _Model(None), TODAY, reject_above=0.5).status == "pending"
