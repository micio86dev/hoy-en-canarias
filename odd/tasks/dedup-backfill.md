# Feature: dedup-backfill

Branch: feat/adaptive-crawling. TDD: strict (session config), runner `uv run pytest`.
Delivery strategy: ask-on-risk. Forecast: ~250 authored changed lines.

## Objective
A CLI command that merges same-source copies already stored before commit aae39e9 (same canonical
official URL, exactly the same start and end), so existing databases lose those duplicates too.

## Problem / why
aae39e9 merges such copies at ingest, but pairs stored earlier stay as two events (flagged
"same source, never auto-merged"); a portal would show them twice.

## Scope
- `deduplication.find_same_source_copies(db) -> list[CopyPair]`: event pairs sharing a source and
  a canonical official URL, with `same_occurrence` true. Keep = the event with more links, then the
  older `first_seen_at`, then the smaller id. Drop must have exactly one link (all its fields come
  from that source); otherwise the pair is reported as skipped with a reason.
- `Database.absorb_event(keep_id, drop_id, source_id, now)`: field merge of the dropped event into
  the kept one through the existing `_merge_into` rules (never null over non-null, priority), move
  the link (`match_method = 'url'`), remove possible_duplicates rows naming the dropped id, delete
  the dropped event row. One transaction.
- CLI `merge-duplicates [--db] [--dry-run]`: prints merged and skipped pairs; dry-run writes nothing.
- README: document the command.

## Constraints
- No guessed fields; no cross-source merges here (ingest rules already cover them).
- No outbound requests.

## Tasks
- [x] B1 finder + absorb + CLI + README (route: delegated direct, one writer; writer trigger: 3+ non-trivial files)
- [x] B2 real check on a copy of the test DB (route: direct inline)

## Acceptance criteria
- Exact copies merge; different end time, different URL, or no URL are untouched.
- Drop with 2+ links is skipped and listed.
- Dry-run leaves the DB byte-identical in row counts.
- Idempotent: a second run finds nothing.

## Progress / evidence

### B1 (route: delegated direct, one sonnet writer; writer trigger: 3 non-trivial source files + tests)
- Writer RED: test_deduplication.py ImportError on find_same_source_copies; 2 CLI tests failed (no command). GREEN 1380 passed.
- Parent spot-check found two defects, fixed with observed RED:
  - absorb_event stamped last_seen_at/last_verified_at with the run time although nothing was fetched -> now keeps the latest real sighting of either copy.
  - the copy could replace a kept non-null value (same-source "update" rule; Segunda jornada venue "Área Recreativa Chanajiga" -> "Chanajiga") -> `fill_only` merge: the kept values always win, the copy only fills nulls (matches README).
- Final: 1381 passed; ruff, format, mypy clean.

### B2 (route: direct inline; copy of scratchpad/test2w/events.sqlite)
- Dry-run: 2 pairs listed, counts unchanged [217, 225, 10]. Run: merged Segunda jornada BTT and Séptima jornada Senderismo -> [215, 225, 8]; both keep image and original venue; integrity_check ok; second run merges 0.

## Next step
Done. RDD assess after commit.
