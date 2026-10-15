# Feature: canarias-events-importer

Objective: Python ETL importing public Canary Islands events for the next Mon-Sun week (Atlantic/Canary). No AI, no HTML scraping, no paid APIs.
Plan: ~/.claude/plans/pasted-content-id-ee84-progetto-jaunty-newt.md
TDD: enabled (strict), runner: `uv run pytest`. Checks: `uv run ruff check .`, `uv run mypy src`.
Delivery strategy: ask-on-risk. Forecast ~2500 authored lines (will need chained slices; ask before exceeding ~400/PR).
RDD: not enabled by user (opt-in, off by default).

## Tasks
- [x] T1 Scaffold (pyproject, uv deps, layout) + date_range (tests first)  — route: delegated writer; commit 83b71db
- [x] T2 Safe HTTP client (SSRF, redirects, size cap, retries, 429, ETag cache) — route: delegated writer; commit 709273f
- [x] T3 Models, normalization helpers (price, html->text), geo island table — route: delegated writer; commits 9630d37 (model + normalize), 5ccecf2 (geo)
- [ ] T4 Adapters: tribe_events, drupal_jsonapi, ckan, ical, rss/atom, ticketmaster + fixtures
- [ ] T5 Database, deduplication, idempotency
- [ ] T6 Exporters, reports, CLI (discover-sources, list-sources, import-events, report)
- [ ] T7 Lint, types, full tests, real import run, inspect outputs
- [ ] T8 README + final summary

## Progress / evidence
- T1: RED `uv run pytest`: ModuleNotFoundError canarias_events.date_range (collection error). GREEN: 27 passed; ruff and mypy clean.
- T2: RED `pytest tests/test_http.py`: ModuleNotFoundError canarias_events.http. GREEN: 60 passed (87 total); ruff, mypy clean.
- T3: RED: ModuleNotFoundError for models/normalize, then geo (collection errors). GREEN: 262 passed total; ruff, mypy (src and tests, strict) clean.
- Environment note: a pre-commit hook (gga) runs an AI review and requires AGENTS.md; a minimal AGENTS.md was added (T1 commit) and its reviews passed. Test files are fully type-annotated at its request.
- Open review suggestions (non-blocking): canonical_url keeps userinfo; La Graciosa/Lanzarote bbox edge near Punta Fariones is coarse.
- Next step: T4 adapters.
