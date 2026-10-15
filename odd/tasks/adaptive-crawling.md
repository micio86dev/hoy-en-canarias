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
- [x] A2R Review follow-ups (route: delegated direct, one writer, 2+ non-trivial files; TDD strict, source: project config/session, runner `uv run pytest`): (1) durations never become start_time; (2) street number before a date is not a day range; (3) offer url resolved against page_url; (4) text after HTML comments kept; (5) blocks grouped by parent element, not id(); (6) html content type only text/html + xhtml; (7) llm verification uses the prompt's reference date; (8) llm HTTPError -> LlmError; (9) renderer fail-closed guard + hook error test; (10) cloudflare fixture IP placeholder; (11) pin Folk Fest date; (12) deterministic pathological-input test
- [x] A3 Source health: history, anomaly detection, quarantine (no export of doubtful data), snapshots w/ retention, per-source frequency, failure backoff, concurrency, dry-run, --no-ai, timings; pipeline status for blocked-with-partial-data and robots_disallowed/needs_manual_review sources (review findings R3-blocked-partial-ingest, R3-robots-ssrf-reported-success)
- [x] A3R Review follow-ups (route: delegated direct, one writer; TDD strict, source: project config/session, runner `uv run pytest`): (1) bare "N horas" after a date is a clock time; (2) day ranges after a line break; (3) all-deferred run exits 0; (4) HTTP 429 is a temporary failure; (5) anomaly baseline recovery; (6) page-limit warning names the limit that applied; (7) status vocabulary defined once; (8) accept_quarantine reuses `_new_stats()`
- [x] A4 Social registry (acquisition modes), Bluesky/Mastodon adapters, import-url command, organizer submissions, discover-sources extension + validate-source
- [ ] A5 Real run, inspect, README update, final summary

## Progress / evidence

### A1 + A2 (route: delegated direct, one writer; 2026-10-15)
Route declaration: A1+A2 touch 40+ files and need fixture reading -> one delegated writer (mapping and write triggers). TDD strict, runner `uv run pytest`; every module was written test-first and the RED state observed before the implementation.

Commits (work units, history rewritten 2026-10-15 to fit the native review budget; content identical):
- a754880 test: fixtures webtenerife; 3bf3df1 build: deps (uv.lock)
- f85781c feat: spanish date and json-ld extraction
- ad998ea, a88c487, 38b08a7, b29689f, 1e01068, cd94a21, d583108, 5c5eb5f, 6019540, 9a306ce, 1525443, a046da1: per-source fixtures (adeje, cact, block page + robots, auditorio tenerife, auditorio lpa, la laguna, lpacultura, arona, fuerteventura, gran canaria, tea, teatro leal + sitemap)
- f9d69f8 feat: generic block and embedded json extraction
- ed91106 feat: robots.txt compliance
- 6b2f728 feat: optional playwright and crawl4ai renderers
- e4fbaa0 feat: optional local ollama extractor
- 708b18c feat: tiered web adapter
- 20f1521 fix: crawl4ai fallback; 9fae170 fix: block handling (found by review)
- d02a7fc chore: configure html sources; 680dc67 docs: record A1 and A2 progress

### RDD review record (reliability lens, all APPROVED + acknowledged, authority burned)
- a754880, f85781c, a88c487, 38b08a7, b29689f, cd94a21, 5c5eb5f, 6019540, 9a306ce: approved
- 1525443..a046da1: approved; f9d69f8: approved; ed91106..e4fbaa0: approved; 708b18c..680dc67: approved
- Not reviewable (lens_context_budget_exceeded, no authority created): 3bf3df1 (uv.lock), ad998ea (192 KB fixture), d583108 (174 KB fixture)
- 1e01068 (340 KB fixture) skipped by decision
- Backups of the old history: backup/adaptive-crawling-pre-split, backup/adaptive-crawling-pre-split2

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

### A2R review follow-ups (route: delegated direct, one writer; TDD strict, runner `uv run pytest`)
Commits: a23011a docs (RDD review record, A2R task); 10bbf6a fix: harden spanish date and time extraction (items 1, 2, 12); e77829e fix: keep text after html comments, resolve offer urls and tighten html content type (items 3, 4, 5, 6, 11); f572de4 fix: align llm verification date, map http errors and fail closed in crawl4ai hook (items 7, 8, 9, 10).
RED observed first for items 1, 2, 3, 4, 6, 7, 8, 9 (hook error wrapping). Items 5, 9 (guard), 11, 12 are regression/pinning tests that pass on the old code: the lxml `id(getparent())` collision could not be reproduced (60..3000 distinct parents never merged), the fix keys by the parent element anyway; the fail-closed guard already existed.
Verification: `uv run pytest`: 879 passed, 20 deselected (baseline 853); `uv run ruff check .`: All checks passed; `uv run mypy src tests`: Success, no issues in 68 source files.


### A3 Source health, anomalies, quarantine, scheduling (route: delegated direct, one writer; 2+ non-trivial files; TDD strict, source: project config/session, runner `uv run pytest`)
Commits: 3964457 feat: record source run history and health report; 4c43c9e feat: detect source anomalies and quarantine doubtful events; 815b190 fix: keep pending quarantine on empty runs and honour history_runs in report --health; 6b6cf32 feat: add source frequency, failure backoff and concurrency; 6266b15 feat: save diagnostic snapshots of failed or anomalous listings; 89c0cd7 feat: add dry-run, no-ai, force and low-resource import options; 7866f0a fix: escape the dry-run database path and validate the backoff cap.

Design (small and boring: SQLite tables + plain functions, no new dependencies):
- Schema v2 `source_runs` (per source per run: status, adapter source_status, events_found, events_in_window, duration, error, strategy, extraction_ok, listing_hash, anomalies); v3 `quarantine` (events of a doubtful run stored as JSON, status pending|accepted|superseded). The `events` table and the exports never see quarantined data; the previous valid state is untouched (no deletes, no `last_seen` bumps).
- Run statuses: succeeded | partial (blocked after some events: events quarantined as `blocked_partial`) | anomalous | failed | robots_disallowed | needs_manual_review. The last two (R3-robots-ssrf-reported-success) are listed in `sources_needs_review`, never in succeeded/failed, never backed off as temporary. R3-blocked-partial-ingest: blocked with events is `partial` (listed in `sources_partial`), blocked with no events stays `failed`. New report keys: sources_partial, sources_anomalous, sources_needs_review, timings, params.{concurrency,low_resource,force}.
- Anomalies (`health.detect_anomalies`, deterministic, vs the last N=10 successful runs; anomalous runs never shape the baseline): empty_after_events (0 events after a source that had events, extraction ok, not a declared empty-marker page), layout_changed (same but listing hash differs), extraction_failed (0 events, extraction_ok False), count_drop (> anomaly_drop_ratio=0.7 below the median, needs >= 3 successful runs and median >= 5). strategy_changed is informational only (reported, never blocks). blocked/robots sources get no content comparison. An empty page is "no events" only when extraction_ok (web adapter) or the page matched `empty_marker`.
- Quarantine: a normal run supersedes pending items; `review-quarantine --list` / `--accept <source>` publishes them through the normal dedup/upsert. A new quarantine of the same source replaces the pending one; an empty anomalous run keeps what is pending. After `--accept`, run `import-events` to re-export.
- Scheduling (`scheduling.skip_reason`): per-source `frequency` (daily|weekly|hourly|<n>h|<n>d, validated at load) vs last success; after `failure_threshold`=3 consecutive temporary failures, backoff base 6h x 2^(n-K), capped 168h; blocked/robots/manual-review/partial sources cool down 72h instead (no loop). Skips are reported in `sources_skipped` with the reason; `--force` ignores all of it.
- Concurrency: ThreadPoolExecutor, `crawl.concurrency` default 4; only network fetching is threaded, results are stored serially in configured order (deterministic outputs). Per-host politeness preserved: `SafeHttpClient` now reserves request slots under a lock (a frozen-clock test proves consecutive slots); `SqliteHttpCache` is lock-guarded and the connection is `check_same_thread=False`.
- Snapshots (off by default, `crawl.snapshots`): raw first listing page of non-healthy runs to `output/snapshots/<source>/<timestamp>.html`, capped at 500 kB, retention 14 days and 10 files per source, pruned at run start and end; only files matching the naming scheme are ever deleted. The project writes no log files (stderr only), so there is no log retention to configure.
- CLI: `import-events --dry-run` (in-memory copy of the DB, no HTTP cache, no snapshots, writes only `dry-run-report.json`; the DB file is byte-identical afterwards), `--no-ai`, `--force`, `--low-resource` (also `crawl.low_resource`: concurrency 1, no renderer, AI off, 15-page cap); `report --health [--config]`; `review-quarantine`. Report timings: per source `fetch_s`/`store_s`, per tier seconds, total.
- Deviations: strategy change alone does not quarantine (too noisy); listing hash is a text hash, so a "radical change" is any change combined with zero events; `last_events_found` etc. are null without history; no separate `--concurrency` flag (config only).

RED -> GREEN per module (`uv run pytest <file>` first run failed on missing symbols/behaviour, then passed): test_health (history roundtrip, summary, anomaly rules 17 tests), test_database (quarantine, thread-safe cache, memory_copy, schema), test_pipeline (history rows, status mapping, quarantine, frequency/backoff/cool-down, concurrency barrier test, snapshots, low-resource, timings), test_scheduling (23), test_snapshots (8), test_http (slot reservation), test_source_web (listing hash, render/page cap), test_cli (report --health, review-quarantine, dry-run, --no-ai, --force, --low-resource, timings), test_sources_config (crawl section).
Verification: `uv run pytest`: 978 passed, 20 deselected (baseline 879); `uv run ruff check .`: All checks passed; `uv run mypy src tests`: Success, no issues found in 75 source files. Live smoke: `import-events --dry-run --source teatro_leal --from 2026-10-19 --to 2026-10-25` (temp db/output dirs): succeeded, 20 received / 8 in window, 1.5 s, only `dry-run-report.json` written, no database created.
Open for A5: README (RAM/CPU of low-resource mode, quarantine workflow), engram mirror sync by the orchestrator, gga reviewer notes not acted on (all minor/optional): `quarantine_close` status Literal, `groups` typing in review-quarantine, snapshot prune assumes a consistent timezone for `now` (always Atlantic/Canary today).

### A3 RDD record
680dc67..45b3a84: high risk, 4 lenses, APPROVED + acknowledged; advisory findings fixed in A3R.

### A3R review follow-ups (route: delegated direct, one writer; TDD strict, runner `uv run pytest`)
Commits: e06a5c1 fix: recognise bare-hour clock times and line-start day ranges (1, 2); c18cd49 fix: keep implausible bare hours after a comma as durations (gga hardening of 1); e5ab66b refactor: share source status vocabulary and reuse run stats in accept_quarantine (7, 8; new `statuses.py`); 078cb73 fix: treat HTTP 429 as a temporary rate limit instead of a blocked cool-down (4); a388c62 fix: name the page limit that stopped a crawl (6); 117259b fix: let anomaly baselines recover after an accepted or stable shrink (5); 65b4f7c fix: exit cleanly when no source is due (3).

Rules:
- Clock vs duration: a bare "N horas" is a clock time after "a las", a month or a year; after a comma or dash only when N >= 7 ("..., 2 horas" stays a length, never 02:00). "N horas y 30 minutos / y media" and every duration cue stay durations. A day-range start is also accepted at a line start.
- 429 is reported by the web adapter as `rate_limited` (own run `source_status`): temporary, counts toward the exponential backoff, no 72h cool-down. 401/403/challenge stay `blocked`. Not done: `Retry-After` is honoured only inside `SafeHttpClient` (capped 60 s) and is not recorded in the run history, so the backoff schedule ignores it.
- Baseline recovery (no schema change): `review-quarantine --accept <source>` records a succeeded run marked `baseline_reset`; and after `crawl.anomaly_stable_runs` (3) consecutive `count_drop` runs within `crawl.anomaly_stable_tolerance` (20%) of their median the third run publishes with `baseline_reset`. The baseline only looks back to the latest `baseline_reset`. Nothing is ever deleted; accepting `blocked_partial` items does not reset the baseline.
- Exit code: report key `sources_deferred`; nothing attempted and something deferred -> "No source due; N skipped." exit 0; attempted and none succeeded, or nothing attempted for other reasons (disabled, missing key) -> exit 1 as before.

RED -> GREEN: test_extract_dates_es 8 failed -> 116 passed (the "Hora: 21 horas" case already passed); test_source_web (429 `rate_limited`, low-resource page cap message) and test_pipeline (rate-limited failure) RED -> GREEN; test_health (5 recovery tests) and test_pipeline (accept baseline, stable self-heal) RED -> GREEN; test_cli (no-source-due) RED -> GREEN. Scheduling tests for rate_limited and the refactor are pinning tests (passed on first run).
Verification: `uv run pytest`: 1013 passed, 20 deselected (baseline 978); `uv run ruff check .`: All checks passed; `uv run mypy src tests`: Success, no issues found in 76 source files.
Open: README (quarantine/baseline workflow, `anomaly_stable_*`) in A5.

### A3R RDD record
45b3a84..410424c: high risk, 4 lenses, APPROVED + acknowledged; advisory findings fixed in A4 step 0.

### A4 Social registry, public social adapters, manual imports, discovery and validation (route: delegated direct, one writer; 2+ non-trivial files; TDD strict, source: project config/session, runner `uv run pytest`)
Commits: a130219 + 83b27c3 fix: A3R follow-ups (step 0); 85fd430 feat: social source registry; 365c457 feat: mastodon and bluesky public adapters; 0154b19 feat: import-url and organizer submission commands; f4fc8f6 feat: discover-sources candidates; 679396b feat: validate-source; d589fb3 fix: unknown licence text, single imported page.

Step 0 (A3R advisory findings): (a) a 429 now yields `rate_limited: ...` in the adapter warning and the pipeline error (`blocked: ...` stays for 401/403/challenge); (b) `statuses.BLOCKED_PARTIAL` replaces the magic string; (c) `accept_quarantine` sets the baseline from the non-`blocked_partial` items of the latest quarantining run only, and accepting only partial items records nothing; (d) the synthetic accept row carries `manual_accept` and the scheduler ignores those rows (a real self-heal success with `baseline_reset` still counts; the gga reviewer asked for a separate marker); (e) `_stable_new_level` never accepts a zero level; (f) bare "N horas" after a month/year context needs N >= 7 ("Sábado 24 de octubre 2 horas" is a length, "24 de octubre, 21 horas" is 21:00); (g) `page_limit_message`.

A4 design (small, boring, nothing auto-registered):
- Social registry: `config/social_sources.yaml` + `social.py` (pydantic, `extra=forbid`). Seeded only with the research-verified platform rows (Facebook Pages requires_authorization, Facebook Groups excluded, Instagram and Meetup requires_authorization, Eventbrite API excluded, Eventbrite public pages import_url, Telegram manual_only, YouTube channel RSS rss, Mastodon RSS rss, Bluesky public API api_public) plus Radio Insular `https://radioinsular.es/feed/` (rss, candidate, news signal). No account rows were invented. `requires_authorization`/`manual_only`/`excluded` cannot be enabled or given a frequency (validation error); `schedule_blocker()` explains why a row is not scheduled. Only `kind: account`, mode api_public/rss, `verified`, `enabled`, with an adapter are turned into `SourceConfig` (priority 10, `frequency` honoured) and appended to `import-events` (`--social-config`, default `social_sources.yaml` next to the config). `list-sources --social` prints the registry.
- Mastodon (account RSS) and Bluesky (public AppView `app.bsky.feed.getAuthorFeed`, `filter=posts_no_replies`, reposts and other authors skipped) share `sources/social_posts.py`. A post is an event only with a deterministic date (`dates_es`) AND (a) a link on a host the person listed in `link_hosts` whose JSON-LD has exactly one Event on that day/month (fetched with the web adapter in `jsonld_only` mode: robots, content-type guard, SSRF guard, max 8 pages per run), or (b) an explicit `en <municipality>` (longest match; the year must be stated unless the account opts into `year_policy: infer`). Otherwise it is skipped and counted in the report (no date, ambiguous dates/places, no page or place). Nothing else is filled in.
- Trust: social events carry `extra.source_trust = "social"` (`models.is_social`). `deduplication.resolve` never fuzzy-merges when either side is social (the near match becomes a `possible_duplicates` row, reason "social source, never auto-merged"); exact source-id and official-URL identities still merge; field conflicts are settled by the existing source priority (social = 10), so social data fills gaps but never overwrites a reliable value, and a reliable source overrides what a social one wrote first.
- `import-url <url>`: refuses Facebook/Instagram hosts before any request (message points to `submit-event`), then SSRF guard, robots, content-type guard, challenge detection and the web adapter tiers (JSON-LD, embedded, blocks); max 25 events; stored in `quarantine` under the pseudo-source `import_url` with reason `needs_review` (never auto-published) unless `--accept`; `--dry-run` prints the JSON and touches no database. `submit-event --file event.json|.ics [--organizer NAME]`: validated by `OrganizerEvent` (`docs/organizer-submission.schema.json`, example next to it; the schema file is tested against the model), provenance `organizer_submission`, unknown municipality/island/keys rejected, all-or-nothing; same hold/accept/dry-run flow. Both are published with `review-quarantine --accept import_url|organizer_submission`; `NO_LEVEL_HOLDS` keeps those holds out of the anomaly baseline; `quarantine_add(replace=False)` lets several imports wait side by side.
- Discovery: new table `source_candidates` (schema v4) holds everything discovery finds with state `candidate`; nothing is enabled. Inputs: positive catalog/probe results, agenda-like links on pages of enabled configured sources (same registrable domain or domains of configured sources, robots-checked, max 10 sources x 10 links), `--url`/`--url-file`, `--social-url` (platform classified by `classify_social_url`, never fetched). `--no-catalogs`, `--no-links`, `--db`; `list-sources --candidates`.
- `validate-source <url|candidate id|source id> [--save] [--json]`: robots, reachability (401/403/challenge = blocked, never bypassed; 429/5xx = temporary, candidate stays), content kind (html/ical/feed/json/other), events through the web/ical/rss adapters (or the configured adapter), completeness (title + start date + place), events in the next 30 days and in the target week, reliability from `source_runs` if the id is configured, reuse terms (`rel=license` or the configured licence; "unknown" otherwise), score 0-100 (presence 30, completeness 25, coverage 20, reliability 15 with unknown = neutral 8, terms 10) and suggested `acquisition_mode`/`verification_state` (verified needs events, >= 60% complete, >= 1 in 30 days; blocked/robots/non-acquirable = rejected). The state is written only with `--save`.

RED -> GREEN (first run of each new test file failed on missing symbols or behaviour): step 0: 7 failing tests (rate_limited text x2, accept baseline, scheduling, zero level, 2 date cases) -> green; test_social_registry 18; test_source_social 23 + geo/dedup trust cases (3 failed before the rule) ; test_manual_import 40; test_candidates 28; test_validation 23; test_cli additions (social list, social import, import-url, submit-event, discover, validate-source). `jsonld_only` RED verified by stashing the source change (2 failed).
Verification: `uv run pytest`: 1199 passed, 20 deselected (baseline 1013); `uv run ruff check .`: All checks passed; `uv run mypy src tests`: Success, no issues found in 89 source files. gga review of every commit: PASSED after fixing its findings (link hosts allow-list for AGENTS.md "HTML only for configured sources", strict year policy, UTC for naive timestamps, longest municipality match, the manual_accept marker).

Live smokes 2026-10-15 (each once, project UA, robots honoured):
- `validate-source https://teatroleal.es/programacion/`: reachable (HTTP 200), robots allowed, 20 events via blocks, 20 in the next 30 days / 8 in the target week; data quality 0% when validated as a bare URL (the listing has no venue; `venue_name` comes from the source's params), score 58, suggested candidate. `validate-source teatro_leal` (configured): 100% quality, score 93, verified (the licence text "unknown - ToS not checked" was reported as a declared licence: fixed in d589fb3, reuse terms now `unknown`).
- `import-url --dry-run https://www.webtenerife.com/agenda/2026/10/fic-2026-taller-de-clown-y-comedia-con-peter-shub/`: 1 event via jsonld, nothing stored (no database file); the page was set as the event's official URL in d589fb3.
- Bluesky `getAuthorFeed?actor=bsky.app` (curl, then `BlueskyAdapter` once, handle not configured): response shape confirmed (`feed[].post.{uri,author.handle,record.{text,createdAt,facets,embed}}`, `reason` on reposts); 13 posts -> 0 events (none had a date and a place), as expected.

Open for A5: README (social registry, link_hosts/year_policy, import-url/submit-event/review flow, discover/validate commands); engram mirror sync by the orchestrator; no verified Canarian Mastodon/Bluesky accounts exist yet, so no account is enabled; Mastodon RSS is covered by fixtures only (no live instance was queried); Radio Insular was unreachable (503) at the earlier check, `validate-source` can re-check it.
