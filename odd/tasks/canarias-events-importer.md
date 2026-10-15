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
- [x] T4 Adapters: tribe_events, drupal_jsonapi, ckan, ical, rss/atom, ticketmaster + fixtures — route: delegated writer; commits 4d30c32 (base, registry), 4a85f4f (tribe, drupal), 3dfc022 (httpx log masking fix), dca022c (ckan, ical, rss, ticketmaster, config), 868a00b (live tests)
- [x] T5 Database, deduplication, idempotency — route: delegated writer (shared with T6); commits af39e19 (canonical_url + redirect header fixes), db685e1 (sqlite, http cache, dedup)
- [x] T6 Exporters, reports, CLI (discover-sources, list-sources, import-events, report) — route: delegated writer; commits cb0115b (pipeline, exporters, reports), 133862a (discovery, logging), 0c6ce3a (CLI)
- [ ] T7 Lint, types, full tests, real import run, inspect outputs
- [ ] T8 README + final summary

## Progress / evidence
- T1: RED `uv run pytest`: ModuleNotFoundError canarias_events.date_range (collection error). GREEN: 27 passed; ruff and mypy clean.
- T2: RED `pytest tests/test_http.py`: ModuleNotFoundError canarias_events.http. GREEN: 60 passed (87 total); ruff, mypy clean.
- T3: RED: ModuleNotFoundError for models/normalize, then geo (collection errors). GREEN: 262 passed total; ruff, mypy (src and tests, strict) clean.
- Environment note: a pre-commit hook (gga) runs an AI review and requires AGENTS.md; a minimal AGENTS.md was added (T1 commit) and its reviews passed. Test files are fully type-annotated at its request.
- Open review suggestions (non-blocking): canonical_url keeps userinfo; La Graciosa/Lanzarote bbox edge near Punta Fariones is coarse.
- T4: RED: ModuleNotFoundError canarias_events.sources.{base,tribe_events,drupal_jsonapi,ckan,ical,rss,ticketmaster} (collection errors); RED for the httpx log leak (`test_httpx_request_log_masks_secrets`, the API key appeared in httpx INFO logs) and for the `La Frontera` geo alias. GREEN: 404 passed (11 live deselected); ruff and `mypy src tests` clean. `uv run pytest -m live`: 11 passed in 16 s (all enabled sources; ticketmaster skipped cleanly without key).
- T4 real-endpoint findings (2026-10-15):
  - Hola Islas Canarias JSON:API: dates are date-only strings (`field_e_start_date`, `field_e_end_date`, `field_e_calculated_end_date`); lat/lon are strings; `field_event_free` is "1"/"0"/null (only "1" is trusted); `include=field_t_islands,field_t_municipality,field_event_category` works (taxonomy `name`); OR-free filters work (`start < end_date_exclusive` and `calculated_end >= start`), sparse fieldsets cut 130 KB to 20 KB per 5 items; page size cap 50, `links.next` absent on the last page. Source typos exist (start `0026-12-06`, start 2016 with end 2026). Some events have several islands/municipalities (island left null, names kept in `extra`). 239 upcoming events through 2027-06, all with island. The event page is `origin + path.alias`; the event's own site is kept in `extra.event_website`, tickets in `ticket_url`.
  - Tribe REST: `timezone` is unreliable (`Europe/London`, `UTC+0`, `Atlantic/Canary`); non-IANA values are read as Canary wall time. `venue` is `[]` or a dict; `next_rest_url` and `total_pages` present. Los Silos, Santa Ursula, Valle Gran Rey: endpoint confirmed, 0 events.
  - iCal feeds: Deportes Tenerife labels local times as `TZID=UTC` (handled with `params.force_timezone`); Antigua uses `Atlantic/Canary` and `VALUE=DATE` with exclusive DTEND.
  - CKAN Tenerife sports: two JSON resources (data and a data dictionary, excluded by name); 3276 records, organizer sometimes a numeric id, no event id (id = hash of title, url, dates, venue, municipality; 6 exact duplicates dropped); URLs repeat across rows.
  - CKAN Gobcan `agenda-cultural` (real id; the plan's schema matched): CSV `agendaproxima.csv`, 5 rows ending 2022-03, metadata modified 2025-03-06, placeholders `--`/`---`; Santa Cruz `agenda-cultural` lives at `/opendata/api/3/action/` (metadata 2019, JSON key `docs`, int dates).
- T4 notable decisions: httpx logs full request URLs at INFO, so a logging filter masking key/token query values was added to http.py; geo gained the alias `La Frontera`; feedparser and recurring_ical_events have no stubs (mypy override).
- Open (non-blocking): `canonical_url` and `same_host_as` ignore scheme/`www` (dedup in T5 should normalize `www.deportestenerife.es` vs `deportestenerife.es`); SafeHttpClient forwards custom headers across an https to http redirect on the same host (no adapter sends secret headers); Ticketmaster never exercised against the real API (no key), only the documented schema; `ckan._with_time` defaults a missing minute to 0 when an hour is present.
- T5/T6 evidence: RED for T5a: 3 failing tests (userinfo, www, https->http header leak). GREEN 409 passed. T5b/T5c were written implementation-first (database.py, deduplication.py before their tests; deviation from strict TDD, compensated by 37 behaviour tests incl. mutation-style cases); T6 followed RED (collection errors: ModuleNotFoundError pipeline, exporters, logging_config, discovery, cli) then GREEN. Final: `uv run pytest` 509 passed (11 live deselected); `uv run ruff check .` clean; `uv run mypy src tests` clean.
- T5 decisions: URL match additionally requires the same start_date (CKAN URLs repeat across rows/dates); same-source similar events are never merged but recorded as possible duplicates; `priority` per source (cabildo CKAN and municipal 80, deportes_tenerife 70, holaislascanarias 60, ticketmaster 30); http_cache keys are masked URLs (no secrets stored); `stale: true` and `discovery_probes` added to sources.yaml.
- Dedup finding: the captured Deportes Tenerife (tribe) and Cabildo CKAN samples share no event (different days: 17 Oct vs 24/25 Oct, 4/7 Oct); the CKAN sample repeats one event under www and bare host (same source: possible duplicate, not merged). The fuzzy cross-source merge is tested with a real CKAN fixture event re-sourced as a Tribe copy.
- discover-sources live run (2026-10-15): 124 requests, 17 positives for manual check: datos.canarias.es (agenda 4, eventos 26, actividades 2136 hits), datos.tenerife.es (agenda 1, eventos 2, actividades 16), Santa Cruz (agenda 1, eventos 1, actividades 104, fiestas 2), datos.gob.es Canary datasets (eventos 17, actividades 39), and Drupal JSON:API node/event responding on visitlapalma.es, lagomera.travel, elhierro.travel (candidate sources, not enabled). 27 probes failed to connect, 56 returned 404. datos.gob.es path `/apidata/catalog/dataset/title/{term}.json?_pageSize=50` verified.
- Open (non-blocking): events missing from a feed stay exported while their date is in the window (never deleted by design); the `errors` count in the discovery summary includes plain 404s; Ticketmaster still never exercised against the real API.
- Next step: T7 real import run and output inspection (parent).
