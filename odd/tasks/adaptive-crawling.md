# Feature: adaptive-crawling

Objective: extend canarias-events-importer with tiered acquisition from HTML portals (JSON-LD, embedded JSON, generic deterministic extraction, optional renderer Crawl4AI/Playwright, optional local Ollama), source health/anomaly quarantine, social source registry + legit social adapters, URL import, extended discovery/validation. Reuse existing models/adapters/db/dedup.
Branch: feat/adaptive-crawling (from feat/importer). TDD strict, runner `uv run pytest`; checks ruff + mypy src tests. Delivery: ask-on-risk; no remote yet.
Constraints: no auth/CAPTCHA/paywall bypass; respect robots.txt; no automatic social registration; AI off by default and local only; no invented data.

## Research evidence (2026-10-15)
- Python 3.12.13 in venv. Crawl4AI 0.9.5 Apache-2.0, ~30 heavy deps (playwright, litellm fork), no slim extra -> optional extra `crawl`, lazy import. Playwright 1.64 lighter -> optional extra `render`. Ollama 0.11.10 installed, server not running; /api/chat `format` JSON schema; via httpx, no dep. dateparser 1.4.3, trafilatura 2.3.1 (optional). extruct rejected (heavy, stale).
- HTML candidates (fixtures in scratchpad html_samples): webtenerife sitemap.xml/ + detail JSON-LD Event (59 Oct/Nov URLs); auditorioteatrolaspalmasgc.es Nuxt payload (+ api subdomain unprobed); server-rendered: adeje (year missing sometimes), teatroleal (no robots.txt), teatenerife, lpacultura (Livewire JSON), arona, cactlanzarote, cabildofuer; JS/AJAX: grancanaria agenda, tenerife.es, auditoriodetenerife (admin-ajax nonce); blocked 403: lanzarote.com, tomaticket (do not bypass); turismolanzarote microdata = placeholder dates (exclude).
- Social: Facebook Page events requires_authorization (PPCA + App Review); FB Groups API removed 2024-04-22 excluded; Instagram requires_authorization; Meetup requires_authorization; Eventbrite API excluded, public event pages JSON-LD manual_only (import-url); Telegram manual_only/requires_authorization; Mastodon RSS + Bluesky public API integrable (no verified Canarian accounts yet); YouTube channel RSS low value; Radio Insular /feed/ news (signal only).

## Tasks
- [x] A1 Extraction core: Spanish date parser (deterministic), JSON-LD Event extractor, embedded JSON walker, generic repeated-block/link extractor, robots.txt checker — pure, fixture-tested
- [x] A2 Web adapter with tiered strategy + optional renderer (Playwright/Crawl4AI lazy) + optional Ollama extractor; configure HTML sources
- [ ] A3 Source health: history, anomaly detection, quarantine (no export of doubtful data), snapshots w/ retention, per-source frequency, failure backoff, concurrency, dry-run, --no-ai, timings
- [ ] A4 Social registry (acquisition modes), Bluesky/Mastodon adapters, import-url command, discover-sources extension + validate-source
- [ ] A5 Real run, inspect, README update, final summary

## Progress / evidence

### A1 + A2 (route: delegated direct, one writer; 2026-10-15)
Route declaration: A1+A2 touch 40+ files and need fixture reading -> one delegated writer (mapping and write triggers). TDD strict, runner `uv run pytest`; every module was written test-first and the RED state observed before the implementation.

Commits (work units, each reviewed by the gga hook):
- 1750eec feat: add spanish date and json-ld extraction (dates_es, jsonld, candidate/util, deps lxml/dateparser/cssselect, extras render/crawl/content, AGENTS.md scope update)
- bb4c53c feat: add generic block and embedded json extraction (embedded, blocks, guards, text)
- fef1ad3 feat: add robots.txt compliance (RFC 9309 matcher, Crawl-delay via SafeHttpClient.set_host_interval)
- 4de4163 feat: add optional playwright and crawl4ai renderers
- aaeb21a feat: add optional local ollama extractor
- d51a4a6 feat: add tiered web adapter with optional renderers (+ SourceFetchResult/FetchContext/pipeline/registry wiring)
- 17b25f9, 3bd3d2e fixes found by review (auto render fallback, robots recheck, block handling)
- 8f32cef chore: configure html sources

RED -> GREEN evidence (`uv run pytest <file>`):
- dates_es: RED `ModuleNotFoundError: canarias_events.extract.dates_es` -> GREEN 82 passed (incl. false-positive and pathological-input cases; a quadratic scan found by an adversarial run was fixed).
- jsonld: RED collection error -> GREEN 44 passed. embedded: RED -> 18 passed. blocks: RED -> first GREEN run 22/30, after fixes 30 passed. guards/text: RED -> 14 passed.
- robots: RED -> 34 passed (reviewer forced RFC 9309 semantics: longest match, wildcards, exact product token, bounded matcher).
- renderers: RED -> 20 passed with fakes (no browser in CI); llm: RED -> 24 passed; web adapter: RED -> 67 passed.
- Full suite after A2: `uv run pytest`: 853 passed, 20 deselected (live); `uv run ruff check .`: clean; `uv run mypy src tests`: clean (was 509 tests before A1).
- crawl4ai 0.9.5 API verified from the wheel (not installed): AsyncWebCrawler(config=BrowserConfig).arun(url, config=CrawlerRunConfig) -> CrawlResultContainer (attribute passthrough), hook `on_page_context_created`.

Decisions / deviations: robots matching is our own RFC 9309 matcher (urllib.robotparser garbles `*` via quote(); the reviewer required wildcard support); webtenerife `price "0"` with empty currency and naive `T00:00` times are treated as unknown/date-only; blocked sources return status `blocked` and the pipeline records them as failed; `tenerife_es_eventos` uses `render: playwright` because the JS heuristic does not flag it.

### Live results 2026-10-15 (window 2026-10-19..25, 1 req/s, project UA, robots honoured)
| source | strategy | events (total / in window) | enabled |
|---|---|---|---|
| webtenerife | detail-jsonld (sitemap, 40 detail pages) | 40 / 9 | yes |
| auditorio_teatro_lpa | embedded (Nuxt payload) | 24 / 3 | yes |
| teatro_leal | blocks | 20 / 8 | yes |
| tea_tenerife | blocks | 12 / 4 | yes |
| adeje_agenda | blocks (year inferred, flagged) | 12 / 4 | yes |
| arona_agenda | blocks (year inferred, flagged) | 25 / 12 | yes |
| lpacultura | blocks (Livewire snapshots hold no titles) | 20 / 10 | yes |
| cact_lanzarote | blocks | 7 / 3 | yes |
| cabildo_fuerteventura_agenda | blocks (free-text dates, inferred) | 10 / 6 | yes |
| grancanaria_agenda | blocks (static worked) | 12 / 3 | no (kept disabled per plan; can be enabled) |
| tenerife_es_eventos | none: JS-loaded results (needs renderer) | 0 / 0 | no |
| auditorio_tenerife | none: admin-ajax list (needs renderer) | 0 / 0 | no |
End-to-end `import-events` over the 8 fast sources: 50 events in window, 0 errors. lanzarote.com and tomaticket: 403 / Cloudflare challenge, documented as excluded and never bypassed; turismolanzarote excluded (placeholder microdata dates).

Open: engram mirror `odd/adaptive-crawling/tasks` must be synced by the orchestrator; Playwright/Crawl4AI paths are fake-tested only (never run against a real browser).
