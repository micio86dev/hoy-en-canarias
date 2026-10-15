"""Per-source health computed from the stored run history (plain functions, no state)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from canarias_events.database import SourceRun

SUCCEEDED = "succeeded"


def summarize(runs: Sequence[SourceRun]) -> dict[str, Any]:
    """Health of one source from its latest runs (newest first)."""
    if not runs:
        return {
            "runs_considered": 0,
            "last_status": None,
            "last_events_found": None,
            "last_success_at": None,
            "error_rate": None,
            "consecutive_failures": 0,
            "last_anomalies": [],
        }
    streak = 0
    for run in runs:
        if run.status == SUCCEEDED:
            break
        streak += 1
    last_ok = next((r for r in runs if r.status == SUCCEEDED), None)
    failed = sum(1 for r in runs if r.status != SUCCEEDED)
    return {
        "runs_considered": len(runs),
        "last_status": runs[0].status,
        "last_events_found": last_ok.events_found if last_ok else None,
        "last_success_at": last_ok.started_at if last_ok else None,
        "error_rate": round(failed / len(runs), 3),
        "consecutive_failures": streak,
        "last_anomalies": list(runs[0].anomalies),
    }
