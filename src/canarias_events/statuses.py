"""The status vocabulary of a source run, defined once.

Run statuses are what the pipeline stores per source and run; adapter statuses are what an
adapter reports about the listing it tried to read (``SourceFetchResult.status``).
"""

from __future__ import annotations

# Run statuses (``source_runs.status``).
SUCCEEDED = "succeeded"
PARTIAL = "partial"  # blocked after some events: held in quarantine
ANOMALOUS = "anomalous"
FAILED = "failed"
ROBOTS_DISALLOWED = "robots_disallowed"
NEEDS_MANUAL_REVIEW = "needs_manual_review"

# Adapter statuses (``SourceFetchResult.status``); the last two double as run statuses above.
ADAPTER_OK = "ok"
BLOCKED = "blocked"  # 401/403 or a challenge page: not bypassed

# A human has to look at these: neither success nor a crash, and never retried as temporary.
NEEDS_REVIEW = frozenset({ROBOTS_DISALLOWED, NEEDS_MANUAL_REVIEW})
# Nobody fixes these by retrying: the source cools down instead of backing off.
NON_TEMPORARY = NEEDS_REVIEW
# The listing was never read, so there is no content to compare with the history.
NO_CONTENT_STATUSES = frozenset({BLOCKED, ROBOTS_DISALLOWED})
