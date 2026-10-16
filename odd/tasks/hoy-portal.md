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
- [x] P2 Publish projection (importer SQLite -> Postgres): schema 0002, slugs, categories, status, stale.
- [x] P3 Search repositories + public endpoints (events, detail, related, map, islands, categories, municipalities, health/ready).
- [x] P4 Worker scheduler (04:00 Canary, DST-safe, advisory lock, bounded retries, import_runs) + `hoy` CLI + admin trigger.
- [x] P5 Nuxt foundation: tokens (single @theme source, contrast-tested), fonts, i18n es/en/it, layout with skip link, language switcher.
- [x] P6 Home + search (URL-synced filters, date presets, island/category chips, pagination) + event cards + voice search.
- [x] P7 Event detail (sanitised text, provenance, related, share) + island and category index/detail pages.
- [x] P8 Map (Leaflet client-only, markercluster, bbox sync, list alternative, opt-in geolocation).
- [ ] P9 SEO/GEO: meta, hreflang, JSON-LD, sitemaps, robots, llms.txt, privacy.
- [ ] P10 Vitest + Playwright e2e.
- [ ] P11 Visual/a11y/Lighthouse pass, README and docs.

## Progress and evidence
- Baseline importer suite before work: 1382 passed, 20 deselected.
- P1: `uv run --package hoy-api pytest apps/api` -> 9 passed (health, settings, extensions on pgvector/pg17, migrations up/down). mypy strict clean on apps/api. Root `ruff check .` clean; root suite 1382 passed.
- P2: publish suite green: 41 passed (projection unit, slug/category/URL rules, Postgres integration for first publish, idempotent republish, frozen slug, stale marking, unsafe URL nulling, status/category persistence, accent-insensitive search). `cd apps/api && uv run mypy` strict clean.

## Deviations from the approved plan (P2)
- `event_aliases` deferred: slugs are frozen at first publish and the importer's `absorb_event` exposes no keep-id, so no redirect source exists yet. Add it when slug changes or merge redirects are needed.
- `search_tsv` is generated in SQL (migration 0002) and is not mapped in the ORM; it is queried with raw SQL.
- `hoy_unaccent` replaces `<`/`>` with spaces: unaccent turns guillemets into `<<`/`>>`, which the text parser drops as HTML tags.
- Category precedence: the source's raw category is matched before any tag.
- The importer is untyped (no `py.typed`); apps/api mypy ignores its missing stubs. Run the API type gate from `apps/api` so its own config applies.
- Verified: the 8-island list in migration 0002 equals `canarias_events.geo.ISLANDS`.
- P3: API suite green: 81 passed (time windows, rate limit, query validation, accent-insensitive search, distance sort/radius, pagination, detail with sanitized description and provenance, related, map bbox, catalogue counts, security headers, CORS allow-list, readiness, 429 with Retry-After). `apps/api` mypy strict clean (37 files).

## Deviations (P3)
- No separate services layer: routers call `repositories/events.py` directly. The query model in `schemas.py` owns validation, so a service layer would only forward values.
- `GET /api/v1/sitemap-data` deferred to P9 (SEO), where its consumer is defined.
- Rate limit is in-process per client IP, and `X-Forwarded-For` is deliberately not trusted. Behind a reverse proxy this needs a trusted-proxy setting before scale-out (documented for P11).
- Events without coordinates sort last under distance sort.
- P4: API suite green: 103 passed. Covers DST-safe 04:00 scheduling, non-blocking advisory lock (overlap skipped and recorded), bounded retries with backoff, timeout as failed attempt, secret redaction in stored errors, lock release, JSON logs, admin trigger (404 disabled / 401 wrong token / 202 recorded manual run), CLI migrate/publish/import-now/worker. `apps/api` mypy strict clean (49 files).

## Deviations (P4)
- The importer runs as its own process (`canarias-events import-events --no-ai`), so its code and CLI are untouched. The API process never imports it at run time.
- The scheduler lives in `hoy worker` (one process). It is not embedded in API replicas. Multi-replica safety comes from the advisory lock, not from process placement.
- Docker services `api` and `worker` are still to be added (with the web service, in the Docker task).
- P5: `apps/web` unit suite green (11 passed: i18n key parity es/en/it, WCAG AA 4.5:1 for all 7 text pairs). `pnpm build` and `pnpm typecheck` (exit 0). SSR verified: `/` `/en/` `/it/` return 200 with `lang` es-ES / en-GB / it-IT and localized skip-link text; `hreflang` alternates present.

## Decisions and deviations (P5)
- pnpm 11 blocks esbuild's build script by default: allowed explicitly in `pnpm-workspace.yaml` (`allowBuilds`), not interactively.
- Vitest 4 + @nuxt/test-utils 4 so that Vite is a single version (8.x) with Nuxt and the Tailwind plugin. Vitest 3 pulled Vite 7 and broke `nuxt typecheck`.
- TypeScript pinned to 5.x: vue-tsc and the Nuxt type chain are validated on 5.x; 7.x not adopted.
- `baseUrl` (for hreflang absolute URLs) is read from NUXT_PUBLIC_SITE_URL at BUILD time. A production build must set it explicitly.
- Dark mode not implemented yet (light palette only); P11 decides whether to add it.
- Nav links to /buscar, /islas, /categorias, /mapa, /acerca, /privacidad are not built yet and currently log Vue Router warnings. They are built in P6 to P9.
- P6: web suite green: 24 passed (locale parity, WCAG contrast, URL state, voice parser es/en/it, Canary date formatting). Typecheck exit 0, build exit 0. Live check with the API on 127.0.0.1:8765: home lists real events, `/buscar?island=Tenerife` returns the 2 Tenerife events, and an unknown slug returns the API error body through the proxy.

## Decisions and deviations (P6)
- The browser never calls the API directly: a read-only Nitro proxy (`server/api/v1/[...path].get.ts`) forwards GET requests to `NUXT_API_BASE` only, with a path allow-list and a timeout.
- Voice search is progressive enhancement: rendered only where `SpeechRecognition` exists; recognised text fills the field for review and is parsed on submit, the same way as typed text.
- Local port 8000 is held by an unrelated Docker Desktop forward on this machine; the API runs on 8765 in local checks.
- Event detail links point to `/evento/<slug>`, built in P7. Until then they 404.
- P7: web suite green (27 passed, including island URL segments). Typecheck and build exit 0. Live checks against the API on 8765: detail 200 with description and related events; unknown slug 404; `/islas` and `/islas/tenerife` 200 with counts; `/islas/atlantis` 404; `/categorias/music` 200; `/categorias/xyz` 404; `/en/islas/gran-canaria` 200 with the English count.

## Findings and deviations (P7)
- Plural messages: ICU `{count, plural, ...}` is not parsed by this module's `vue-i18n` and crashed SSR for the index pages. Replaced with explicit `One`/`Other` keys and `countKey()`. Found by running the dev server, not the production build.
- Upstream error bodies that are not JSON now map to 502 instead of throwing.
- Navigation paths `/islas` and `/categorias` are not localised yet (`/en/categorias`). Localised route names are a P9 item.
- Municipality pages are not separate routes: municipality chips link to `/buscar?island=…&municipality=…`.
- P8: web suite green (33 passed: coordinate validation, no invented positions, default view covers all eight islands, bbox formatting and clamping). Typecheck and build exit 0. `/mapa` SSR 200 with the list alternative populated from the API; the map itself loads client-only.

## Decisions and deviations (P8)
- Leaflet and markercluster load with dynamic imports on the client only. Popups are built with textContent, not innerHTML, because titles come from external sources.
- Geolocation requires an explicit tap, centres the map and is never stored.
- Default tiles are OpenStreetMap for development (`NUXT_PUBLIC_TILE_URL` overrides at runtime). OSM's tile policy does not permit heavy production traffic; a paid or self-hosted provider is needed for production.
- The in-browser map render is not yet verified: it is covered by the Playwright suite (P10) and the visual pass (P11).
