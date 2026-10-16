# Feature: hoy-portal

Locator: `odd/tasks/hoy-portal.md` (branch `feat/hoy-portal`, from `feat/adaptive-crawling`).

## Objective
Public, multilingual (es/en/it) event discovery portal for the Canary Islands: FastAPI + Nuxt 4 + PostgreSQL/pgvector, daily 04:00 Atlantic/Canary import, search, map, SEO/GEO.

## Constraints
- Importer (`src/canarias_events`, SQLite) stays intact; a publish step projects into Postgres.
- Strict TDD (RED before GREEN), ruff, mypy strict; no Co-Authored-By; no push.
- No deploy, no domain, no paid services. Pin all Docker images.

## Resolved mode
- TDD: enabled (AGENTS.md, global config). Runner: `uv run --package hoy-api pytest apps/api`.
- Route: delegated direct per task; parent verifies.

## Tasks
- [x] P1 Workspace + compose (pgvector 0.8.1-pg17) + settings + Alembic baseline (extensions) + health endpoint.
- [ ] P2 Publish projection (importer SQLite -> Postgres): schema, slugs, categories, status, aliases, stale.
- [ ] P3 Search repositories/services + public endpoints.
- [ ] P4 Worker scheduler (04:00 DST-safe, advisory lock) + CLI + admin endpoint.
- [ ] P5 Nuxt foundation: tokens, fonts, i18n es/en/it, layout.
- [ ] P6 Home + search + cards + filters + voice search.
- [ ] P7 Event detail + island/municipality/category pages.
- [ ] P8 Map (Leaflet, clustering, list alternative, opt-in geolocation).
- [ ] P9 SEO/GEO: meta, hreflang, JSON-LD, sitemaps, robots, llms.txt, privacy.
- [ ] P10 Vitest + Playwright e2e.
- [ ] P11 Visual/a11y/Lighthouse pass, README and docs.

## Progress and evidence
- Baseline importer suite before work: 1382 passed, 20 deselected.
- P1: `uv run --package hoy-api pytest apps/api` -> 9 passed (health, settings, extensions on pgvector/pg17, migrations up/down). mypy strict clean on apps/api. Root `ruff check .` clean; root suite 1382 passed.
