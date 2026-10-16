from dataclasses import dataclass
from typing import Any

from canarias_events.http import FetchError, SafeHttpClient

JEV_HOST = "api.typesafe.ai"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
SPAM_QUESTION = (
    "Is this text spam, an advertisement, or offensive content rather than a real public event? "
    "Answer with the probability that it is."
)


@dataclass(frozen=True)
class Verdict:
    probability: float | None

    @property
    def usable(self) -> bool:
        return self.probability is not None


def read_verdict(payload: Any) -> Verdict:
    """Read the probability defensively. Anything unexpected is unknown, never guessed."""
    try:
        value = payload["answers"]["q"]["probability"]
    except (KeyError, TypeError):
        return Verdict(None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return Verdict(None)
    if not 0.0 <= float(value) <= 1.0:
        return Verdict(None)
    return Verdict(float(value))


class JevModerator:
    def __init__(self, client: SafeHttpClient, api_key: str | None) -> None:
        self._client = client
        self._api_key = api_key

    def spam_probability(self, text: str) -> Verdict:
        """Ask jev about text that already has its personal data removed. Unknown on any failure."""
        if not self._api_key:
            return Verdict(None)
        payload = {
            "state": text,
            "model": JEV_MODEL,
            "questions": {"q": {"type": "noul", "instructions": SPAM_QUESTION}},
        }
        try:
            raw = self._client.post_json(
                JEV_URL,
                payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                allowed_hosts={JEV_HOST},
            )
        except FetchError:
            return Verdict(None)
        return read_verdict(raw)
