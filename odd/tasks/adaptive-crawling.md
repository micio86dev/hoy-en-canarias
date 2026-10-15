# Feature: adaptive-crawling

Objective: extend canarias-events-importer with tiered acquisition from HTML portals (JSON-LD, embedded JSON, generic deterministic extraction, optional renderer Crawl4AI/Playwright, optional local Ollama), source health/anomaly quarantine, social source registry + legit social adapters, URL import, extended discovery/validation. Reuse existing models/adapters/db/dedup.
Branch: feat/adaptive-crawling (from feat/importer). TDD strict, runner `uv run pytest`; checks ruff + mypy src tests. Delivery: ask-on-risk; no remote yet.
Constraints: no auth/CAPTCHA/paywall bypass; respect robots.txt; no automatic social registration; AI off by default and local only; no invented data.

## Research evidence (2026-10-15)
- Python 3.12.13 in venv. Crawl4AI 0.9.5 Apache-2.0, ~30 heavy deps (playwright, litellm fork), no slim extra -> optional extra `crawl`, lazy import. Playwright 1.64 lighter -> optional extra `render`. Ollama 0.11.10 installed, server not running; /api/chat `format` JSON schema; via httpx, no dep. dateparser 1.4.3, trafilatura 2.3.1 (optional). extruct rejected (heavy, stale).
- HTML candidates (fixtures in scratchpad html_samples): webtenerife sitemap.xml/ + detail JSON-LD Event (59 Oct/Nov URLs); auditorioteatrolaspalmasgc.es Nuxt payload (+ api subdomain unprobed); server-rendered: adeje (year missing sometimes), teatroleal (no robots.txt), teatenerife, lpacultura (Livewire JSON), arona, cactlanzarote, cabildofuer; JS/AJAX: grancanaria agenda, tenerife.es, auditoriodetenerife (admin-ajax nonce); blocked 403: lanzarote.com, tomaticket (do not bypass); turismolanzarote microdata = placeholder dates (exclude).
- Social: Facebook Page events requires_authorization (PPCA + App Review); FB Groups API removed 2024-04-22 excluded; Instagram requires_authorization; Meetup requires_authorization; Eventbrite API excluded, public event pages JSON-LD manual_only (import-url); Telegram manual_only/requires_authorization; Mastodon RSS + Bluesky public API integrable (no verified Canarian accounts yet); YouTube channel RSS low value; Radio Insular /feed/ news (signal only).

## Tasks
- [ ] A1 Extraction core: Spanish date parser (deterministic), JSON-LD Event extractor, embedded JSON walker, generic repeated-block/link extractor, robots.txt checker — pure, fixture-tested
- [ ] A2 Web adapter with tiered strategy + optional renderer (Playwright/Crawl4AI lazy) + optional Ollama extractor; configure HTML sources
- [ ] A3 Source health: history, anomaly detection, quarantine (no export of doubtful data), snapshots w/ retention, per-source frequency, failure backoff, concurrency, dry-run, --no-ai, timings
- [ ] A4 Social registry (acquisition modes), Bluesky/Mastodon adapters, import-url command, discover-sources extension + validate-source
- [ ] A5 Real run, inspect, README update, final summary

## Progress / evidence
(none yet)
