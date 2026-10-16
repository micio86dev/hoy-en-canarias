# Canarias Events Importer

Python ETL that imports **public events of the Canary Islands** for the next Monday-Sunday week
(Atlantic/Canary time), deduplicates them deterministically, stores them in SQLite and exports
JSON, CSV and reports.

Hard constraints: **no paid APIs**; AI is **optional, local only (loopback Ollama), off by
default** and never sees raw HTML; HTML is read only from sources listed in `config/sources.yaml`,
with deterministic parsers first, honouring `robots.txt` and never bypassing blocks.

## Contents

[Install](#install) | [Run](#run) | [Acquisition pipeline](#acquisition-pipeline) |
[Next-week rule](#next-week-rule) | [Free keys](#free-api-keys) | [Add a source](#add-a-source) |
[Social platforms](#social-platforms) | [Manual import](#manual-import-and-organizer-submissions) |
[Health and quarantine](#source-health-anomalies-and-quarantine) |
[Discovery and validation](#discovery-and-validation) | [Scheduling](#scheduling) |
[Options](#run-options) | [Extras](#optional-extras) | [Query data](#query-the-data) |
[Reports](#reports) | [Map points](#map-points) | [Deduplication](#deduplication) | [Security](#security) |
[Source status](#source-status) | [Requirements](#requirements-and-measured-resources) |
[Cost](#operating-cost) | [Testing](#testing) | [Known issues](#limitations-and-known-issues)

## Install

Requires Python >= 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env     # optional: TICKETMASTER_API_KEY, CANARIAS_DB_PATH
```

## Run

All commands work as `uv run canarias-events <command>` (console script) or
`uv run python -m canarias_events.cli <command>`.

| Command | What it does |
|---|---|
| `import-events` | Fetch enabled sources, deduplicate, store, export the window's events. |
| `list-sources` | Configured sources (`--social`: social registry, `--candidates`: discovered candidates). |
| `discover-sources` | Find candidate sources (catalogs, probes, links on configured pages, your URLs). Never enables anything. |
| `validate-source` | Score a URL, candidate id or configured source id; `--save` records the verdict. |
| `import-url` | Import one public event page you hand over (held for review). |
| `submit-event` | Store an event an organizer sent as JSON or iCal (held for review). |
| `review-quarantine` | `--list` or `--accept <source>` for events held back. |
| `report` | Summary of a stored run (`--run-id`), or per-source health (`--health`). |

```bash
# Next Monday-Sunday week, all sources, all islands
uv run canarias-events import-events

# Explicit window (inclusive dates)
uv run canarias-events import-events --from 2026-10-19 --to 2026-10-25

# One island, one or more sources (repeat --source)
uv run canarias-events import-events --island Fuerteventura
uv run canarias-events import-events --source holaislascanarias --source candelaria

# Custom database and output folder
uv run canarias-events import-events --db /var/data/events.sqlite --output-dir /var/data/out

uv run canarias-events list-sources
uv run canarias-events discover-sources
uv run canarias-events report --run-id 3
uv run canarias-events report --health
uv run canarias-events import-events --dry-run --no-ai --source teatro_leal
```

Global option: `--log-level DEBUG|INFO|WARNING` (or `CANARIAS_LOG_LEVEL`). Other options:
`--config` (default `config/sources.yaml`). `--from` and `--to` must be given together.

Outputs (default `output/`, written atomically): `events.json`, `events.csv` (UTF-8 with BOM, for
Excel), `events.geojson` (map points), `import-report.json`, `sources-report.json`; `discover-sources` writes
`discovery-report.json`. The database defaults to `data/events.sqlite` (or `$CANARIAS_DB_PATH`).
`import-events` exits with code 1 when no source succeeded.

## Acquisition pipeline

Each `adapter: web` source tries the cheapest reliable tier first and stops at the first that
yields events. The strategy used is stored per run (`report --health`, `import-report.json`).

| Tier | What it reads | Needs |
|---|---|---|
| 1. API / feeds | Open-data APIs, Tribe REST, Drupal JSON:API, iCal, RSS (separate adapters) | nothing |
| 2. JSON-LD | `schema.org/Event` in the page, or in detail pages found through a sitemap (`detail-jsonld`) | nothing |
| 3. Embedded JSON | Framework payloads such as `__NUXT_DATA__` | nothing |
| 4. Deterministic blocks | Repeated blocks and links, Spanish date parser; missing fields stay empty | nothing |
| 5. Renderer (optional) | Only if tiers 2-4 found nothing and the source asks for it (`params.render: playwright\|crawl4ai\|auto`); tiers 2-4 then run again on the rendered page | extras `render` / `crawl` |
| 6. Local model (optional) | Ollama, only on pages a source opted into (`params.ai: true`) and only if `ai.enabled` | Ollama |
| 7. Manual review | Nothing is invented: unclear items stay out; `import-url`, `submit-event`, quarantine | a person |

Notes: this is the order the code runs (static tiers first; the renderer and the model are
last resorts). Web sources have priority 40, so deduplication
never lets scraped data overwrite an API or open-data value. A year that the page does not state
is inferred only with `year_policy: infer` and flagged in `extra.year_inferred`.

## Next-week rule

The default window is the **next Monday 00:00 strictly after today** up to the following Monday
00:00 (half-open, Atlantic/Canary). Example: run on **2026-10-15** (Thursday) -> **19 to 25 Oct
2026**. Run on a Monday and you get the Monday of the *following* week, never the current one.

Overlap semantics: an event is in the window when it intersects it, so a festival running
12-22 Oct appears. All-day events use local dates (an inclusive date-only end covers the whole
last day). An event without end is a point in time.

## Free API keys

Only Ticketmaster needs a key; without it the source is skipped with a warning.

1. Register at <https://developer.ticketmaster.com/> and create an app (Discovery API).
2. Free quota: **5000 calls/day, 5 requests/second**.
3. Put it in `.env` (git-ignored, **never commit it**): `TICKETMASTER_API_KEY=...`
4. ToS: no revenue use, cache for reasonable periods only, attribution and link back to
   Ticketmaster.

## Add a source

Edit `config/sources.yaml`. Fields: `id`, `name`, `adapter`, `url`, `homepage`, `islands`,
`municipality`, `categories`, `license`, `priority` (higher wins field conflicts, default 50),
`enabled`, `stale`, `auth_env`, `params`, `notes`.

```yaml
# 1. WordPress site with The Events Calendar (Tribe REST) - 4 lines
- id: my_town
  name: Ayuntamiento de My Town
  adapter: tribe_events
  url: https://www.mytown.es/wp-json/tribe/events/v1/events
  islands: [Tenerife]
  municipality: My Town

# 2. CKAN open-data dataset (profiles: tenerife_sports, gobcan_agenda; field_map overrides)
- id: my_dataset
  name: My open data agenda
  adapter: ckan
  url: https://datos.example.es/api/3/action/package_show?id=agenda
  license: CC-BY
  params:
    resource_format: JSON        # or resource_id, records_key, field_map: {title: nombre, ...}

# 3. iCal feed
- id: my_ical
  name: My calendar
  adapter: ical
  url: https://www.example.es/events/?ical=1
  islands: [Fuerteventura]
  params:
    force_timezone: Atlantic/Canary   # when the feed labels local times with a wrong TZID
```

Available adapters (`src/canarias_events/sources/`): `tribe_events`, `drupal_jsonapi`, `ckan`,
`ical`, `rss` (event dates via `params.date_field` / `params.end_field`; publication dates are
never used), `ticketmaster`.

**Check before enabling**: terms of use, license, data quality, and that the endpoint answers
(`discover-sources` helps finding candidates but enables nothing).

**When to write a new adapter**: the source is a documented API or feed that none of the above
parses. Subclass `SourceAdapter` (`sources/base.py`), set `source_type`, implement
`fetch(ctx: FetchContext) -> SourceFetchResult` using `ctx.client` (the safe HTTP client), build
events with `result.add_event(...)` (invalid records are discarded and counted, not fatal), and
register it in `ADAPTERS` (`sources/registry.py`). Write the tests first, with fixtures.
Never guess missing fields: use `None`.

### HTML portals (`adapter: web`)

```yaml
- id: teatro_leal
  name: Teatro Leal
  adapter: web
  url: https://www.teatroleal.es/programacion/
  islands: [Tenerife]
  priority: 40
  frequency: daily            # optional: skip until due (daily|weekly|hourly|<n>h|<n>d)
  params:
    venue_name: Teatro Leal
    year_policy: strict       # strict | infer
```

Other `params`: `sitemap_url` + `sitemap_url_pattern` + `max_detail_pages`, `listing_urls`,
`render`, `max_pages_per_run`, `empty_marker`, `ai`, `hints`. Unknown keys are rejected.

## Social platforms

`config/social_sources.yaml` documents every platform and holds the accounts a person verified.
Nothing is registered automatically; `list-sources --social` prints it.

| Platform | Mode | Reason (research of 2026-10-15) |
|---|---|---|
| Facebook Pages | `requires_authorization` | Needs a Meta app with Page Public Content Access, App Review and the page owner's consent. |
| Facebook Groups | `excluded` | Groups API removed by Meta on 2024-04-22; content is private. |
| Instagram | `requires_authorization` | Only professional accounts that authorise a Meta app; no public event data. |
| Meetup | `requires_authorization` | API needs OAuth / Pro plan. |
| Eventbrite API | `excluded` | No public event search. |
| Eventbrite public pages | `import_url` | JSON-LD on `/e/` pages; one page at a time with `import-url`. |
| Telegram | `manual_only` | No public event feed. |
| YouTube channel RSS | `rss` | Public feeds, little value (no event dates); none configured. |
| Mastodon account RSS | `rss` | Public, no auth; adapter `mastodon`. No verified Canarian account yet. |
| Bluesky public API | `api_public` | Public AppView, no auth; adapter `bluesky`. No verified Canarian account yet. |

- `requires_authorization`, `manual_only` and `excluded` can never be enabled or given a
  frequency (validation error). Facebook and Instagram pages are refused by `import-url`.
- Only `kind: account` rows with mode `api_public`/`rss`, `verification_state: verified` and
  `enabled: true` run in `import-events`. `html` and `ical` are valid modes for candidates
  (`validate-source`) but those sources belong in `sources.yaml`.
- A post becomes an event only with a deterministic date and either a linked event page (host in
  the row's `link_hosts`, JSON-LD event on the same full date) or an explicit "en <municipality>".
  Social events carry `extra.source_trust = "social"`, have priority 10 and are never fuzzy-merged.
- Mastodon is covered by fixtures only; Bluesky's response shape was checked once live.
- A broken `social_sources.yaml` is skipped with a warning (in the run output and
  `import-report.json`); official sources still run.

## Manual import and organizer submissions

```bash
uv run canarias-events import-url https://www.eventbrite.es/e/... --dry-run   # print only
uv run canarias-events import-url <url>                                      # hold for review
uv run canarias-events submit-event --file event.json                         # or .ics + --organizer
uv run canarias-events review-quarantine --list
uv run canarias-events review-quarantine --accept import_url   # or organizer_submission
```

`import-url` goes through the SSRF guard, robots.txt, the content-type guard and the web tiers
(max 25 events). Refusals (private address, PDF, Facebook/Instagram, oversized body) exit with
code 2 and a one-line message. Events wait in quarantine unless `--accept` is given. The
organizer file schema is `docs/organizer-submission.schema.json` (example next to it); it is
validated all-or-nothing and unknown municipalities, islands or keys are rejected.

## Source health, anomalies and quarantine

Every source run is recorded (`source_runs`: status, strategy, counts, duration, error).
`report --health` prints per-source runs, last status, last success, events, error rate, failure
streak and anomalies.

- Statuses: `succeeded`, `partial`, `anomalous`, `failed`, `robots_disallowed`,
  `needs_manual_review`.
- Anomalies (compared with the last successful runs): `empty_after_events`, `layout_changed`,
  `extraction_failed`, `count_drop` (> 70% below the median, needs >= 3 runs and median >= 5).
- A doubtful run is **quarantined**: its events never reach the database or the exports and the
  previous valid state is kept. Inspect with `review-quarantine --list`; publish with
  `--accept <source>`, which also resets that source's baseline; then run `import-events` to
  re-export.
- After 3 consecutive stable `count_drop` runs the new level becomes the baseline by itself.

## Discovery and validation

```bash
uv run canarias-events discover-sources [--url URL] [--url-file F] [--social-url URL] \
    [--no-catalogs] [--no-links]
uv run canarias-events list-sources --candidates
uv run canarias-events validate-source <url|candidate id|source id> [--save] [--json]
```

Workflow: **candidate -> verified -> you add it to `sources.yaml`**. `discover-sources` searches
open-data catalogs, probes known domains, reads agenda-like links on pages of configured sources
(robots-checked, same domain, at most 10 x 10) and your URLs; it never crawls the web and
never enables anything. `validate-source` checks robots, reachability (401/403/challenge =
blocked, never bypassed), content kind, events found, completeness (title + date + place),
coverage, reliability and reuse terms, then scores 0-100 and suggests an `acquisition_mode`
(`html`, `ical`, `rss`, `api_public`, `manual_only`, ...) and a state. `--save` writes it to the
candidate; after an inconclusive check (outage, 429/5xx, timeout, robots unreadable, parser
error) the stored state, mode and score are kept and only the check time and error are recorded.

## Query the data

```bash
sqlite3 data/events.sqlite
```

```sql
-- Events overlapping the window 2026-10-19..2026-10-25 (dates are YYYY-MM-DD)
SELECT start_date, island, title, source_name FROM events
WHERE start_date <= '2026-10-25' AND COALESCE(end_date, start_date) >= '2026-10-19'
ORDER BY start_date, island;

-- Which sources reported each event (an event merged from several sources has several rows)
SELECT e.title, es.source_id, es.match_method, es.official_event_url
FROM event_sources es JOIN events e ON e.id = es.event_id
ORDER BY e.title;

-- Pairs that look similar but were NOT merged (review manually)
SELECT a.title AS a_title, b.title AS b_title, p.score, p.reason
FROM possible_duplicates p
JOIN events a ON a.id = p.event_id_a JOIN events b ON b.id = p.event_id_b
ORDER BY p.score DESC;
```

Other tables: `sources`, `import_runs` (the full report of each run), `http_cache`.

## Reports

`output/import-report.json` (per run):

- `run_id`, `executed_at`, `window` (`start`, `end`, `start_date`, `end_date_inclusive`),
  `params` (`island`, `sources`, `merge_threshold`)
- `sources_attempted`, `sources_succeeded`, `sources_failed`, `sources_skipped` (`id`, `reason`)
- `sources.<id>`: `status`, `records_received`, `valid_events`, `in_window`, `new`, `updated`,
  `unchanged`, `merged`, `possible_duplicates`, `discarded` (reason -> count), `duration_s`,
  `pages`, `warnings`, `error`
- `totals`: `events_in_window`, `records_received`, `valid_events`, `in_window`, `new`,
  `updated`, `unchanged`, `merged`, `possible_duplicates`, `discarded`
- `errors`, `warnings`
- `sources_partial`, `sources_anomalous`, `sources_needs_review`, `sources_deferred`; per source
  also `source_status`, `strategy`, `extraction_ok`, `anomalies`, `quarantined`, `tiers`,
  `timings`; top-level `timings` (per source and per tier) and `params` (`concurrency`,
  `low_resource`, `force`)

`output/sources-report.json`: `generated_at`; `sources[]` with `id`, `name`, `adapter`, `url`,
`homepage`, `status`, `enabled`, `islands`, `license`, `priority`, `auth` (`required`, `env`,
`configured`), `last_result` (`last_success_at`, `last_error`, `last_run_id`), `notes`;
`candidates[]` and `excluded[]` with `name`, `url`, `reason`, `checked_at`.

`output/discovery-report.json`: `generated_at`, `notes`, `results[]` (`endpoint`, `status`,
`kind`, `items_found`, `needs_manual_check`, optional `error`), `summary` (`requests`,
`needs_manual_check`, `errors`).

## Scheduling

Run weekly, e.g. Friday 06:00 Atlantic/Canary, so the next week is ready for the weekend.

cron (set `CRON_TZ`; use absolute paths):

```cron
CRON_TZ=Atlantic/Canary
0 6 * * 5 cd /path/to/canarian_events && /usr/local/bin/uv run canarias-events import-events >> logs/import.log 2>&1
```

macOS launchd, `~/Library/LaunchAgents/es.canarias.events.plist` (launchd uses the system
timezone; set it to Atlantic/Canary or adjust the hour). Load with
`launchctl load ~/Library/LaunchAgents/es.canarias.events.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>es.canarias.events</string>
  <key>WorkingDirectory</key><string>/path/to/canarian_events</string>
  <key>ProgramArguments</key><array>
    <string>/usr/local/bin/uv</string><string>run</string>
    <string>canarias-events</string><string>import-events</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Weekday</key><integer>5</integer>
    <key>Hour</key><integer>6</integer><key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>/path/to/canarian_events/logs/import.log</string>
  <key>StandardErrorPath</key><string>/path/to/canarian_events/logs/import.log</string>
</dict></plist>
```

Per-source scheduling (all optional, in `sources.yaml`):

- `frequency: daily|weekly|hourly|<n>h|<n>d` skips a source until it is due (last real fetch).
- After 3 consecutive temporary failures (including HTTP 429) a source backs off: 6 h x 2^(n-3),
  capped at 168 h. Blocked, robots-disallowed, manual-review and partial sources cool down 72 h.
- `crawl.concurrency` (default 4) fetches sources in parallel; per-host politeness (1 request per
  second per host) still applies. Results are stored serially in configured order.
- `--force` ignores frequency, backoff and cool-downs. If nothing is due, the run exits 0.

Imports are idempotent: a second run on the same window reports 0 new and 0 updated.

## Run options

| Option | Effect |
|---|---|
| `--dry-run` | Fetch and report on an in-memory copy of the database; nothing stored or exported except `dry-run-report.json`. |
| `--no-ai` | Never use the local model, whatever the config says. |
| `--force` | Ignore frequency, backoff and cool-downs. |
| `--low-resource` | One source at a time, no renderer, no model, at most 15 pages per source (also `crawl.low_resource`). |
| `--from/--to`, `--island`, `--source` | Window and filters. |

Diagnostic snapshots (raw first listing page of failed or anomalous runs) are off by default.
Enable with `crawl.snapshots.enabled: true` in `sources.yaml`; files go to
`output/snapshots/<source>/`, capped at 500 kB, kept 14 days and 10 per source. The project
writes no log files (stderr only), so there is no log retention to configure.

## Optional extras

```bash
uv sync --extra render      # Playwright (then: uv run playwright install chromium)
uv sync --extra crawl       # Crawl4AI (heavy: about 30 dependencies)
uv sync --extra content     # trafilatura
```

All are imported lazily; without them the importer runs normally. The Playwright and Crawl4AI
paths are tested with fakes only and were **never run against a real browser**.

**Local model (Ollama), off by default.** Install Ollama, `ollama pull qwen2.5:7b`, then:

```yaml
# sources.yaml
ai:
  enabled: true
  model: qwen2.5:7b            # default; base_url must be loopback (http://127.0.0.1:11434)
```

and set `params.ai: true` on the sources allowed to use it. The model gets cleaned page text
(never raw HTML), its answer must be JSON in a fixed schema and is verified against the text.
Environment overrides: `CANARIAS_AI_ENABLED`, `_BASE_URL`, `_MODEL`, `_TIMEOUT`,
`_MAX_INPUT_CHARS`. **RAM for a 7B model is an unverified estimate (several GB); it was not
measured here and the AI tier has not been run live.**

## Map points

Every exported event may carry a derived map point, separate from the source fields:
`latitude`/`longitude` are **source-only** and are never modified. The point is in `events.json`
(`map`: `latitude`, `longitude`, `precision`, `basis`, or `null`), in `events.csv` (last columns
`map_latitude`, `map_longitude`, `map_precision`) and in `events.geojson` (a FeatureCollection,
coordinates `[longitude, latitude]`; events without a point are left out).

Precision, in order of preference:

1. `source`: the event has both coordinates from its source.
2. `venue`: `venue_name` equals a name or alias in `config/venues.yaml` (case, accents and
   punctuation ignored, no fuzzy matching) and the event's municipality, when known, is the
   venue's municipality.
3. `municipality`: the event's municipality has a reference point (and its island, when known,
   agrees). **Approximate**: it is the municipality's main town, not the event location, so draw
   it differently (larger marker, clustering) from `source` and `venue` points.

Otherwise there is no point: nothing is guessed from the title or from the island alone.

Provenance: municipality points are Wikidata P625 (CC0, fetched 2026-10-16, in
`canarias_events/geo_points.py`); venues use Wikidata P625 too, with the item in `source`.

Add a venue by appending to `config/venues.yaml` (`name`, optional `aliases`, `municipality`,
`island`, `latitude`, `longitude`, `source`). Loading fails when the municipality is unknown or
the coordinates fall outside the declared island. Use verified coordinates only.

## Deduplication

Deterministic, no AI. For each incoming event, in order:

1. Same `(source, source event id)`: update of a known record.
2. Same canonical official URL **and** same start date, from another source: same event.
   From the same source (a feed that publishes one event twice, e.g. with and without `www.`)
   it is merged only when start and end are exactly the same.
3. Same start/end dates, title similarity (token-sort ratio, normalized titles) >= 92, agreeing
   location (same municipality, or coordinates within 300 m, or similar venue name) and
   compatible start time: merged.

Near misses (similarity 75-92, or >= 92 with a different location or start time) stay separate
and are recorded in `possible_duplicates`. Apart from those exact copies, two events of the
**same source** are never auto-merged. When sources disagree on a field, the source with higher
`priority` wins; the provenance of each field is stored. The threshold is in `import-report.json` (`merge_threshold`).

## Security

- Only `http`/`https`; every request goes through `SafeHttpClient`.
- SSRF guard: private, loopback and link-local addresses are refused.
- Redirects are followed manually, each hop re-validated; response size is capped (10 MB
  default, 2 MB for discovery); retries with backoff; `429` respected; per-host rate limit.
- Secrets are masked: key/token query values are redacted in logs (including httpx logs) and the
  HTTP cache stores masked URLs only.
- No credentials in the repository: `.env` is git-ignored.
- `robots.txt` is honoured (RFC 9309 matching); 401/403/challenge pages mean `blocked` and are
  never bypassed: no stealth, no CAPTCHA solving, no logins, no paywall tricks.
- Web page content is data, never instructions: it only feeds deterministic parsers (or the
  optional local model, restricted to a fixed JSON schema and verified against the text).
- Renderers validate every request and redirect hop with the same SSRF guard; the model endpoint
  must be loopback.

## Source status

Counts are from the real run of **2026-10-15** (window 19-25 Oct 2026, Atlantic/Canary):
received / valid / in window. 126 distinct events overlap the window (78 start inside it, 48 are
ongoing events that started earlier).

### API, open data and feeds (69 events)

| Source | Adapter | Islands | Result | License |
|---|---|---|---|---|
| `holaislascanarias` | drupal_jsonapi | all 8 | 50 / 50 / 50 | unknown (ToS not published) |
| `cabildo_tenerife_deportes` | ckan | Tenerife | 3276 / 3270 / 7 | CC-BY (attribution required) |
| `deportes_tenerife` | tribe_events | Tenerife | 3 / 3 / 3 (merged into Cabildo events) | none stated |
| `candelaria` | tribe_events | Tenerife | 8 / 8 / 8 | none stated |
| `santiago_del_teide`, `granadilla_de_abona` | tribe_events | Tenerife | 2 / 2 / 2 and 1 / 1 / 1 | none stated |
| `antigua` | tribe_events | Fuerteventura | 1 / 1 / 1 | none stated |
| `los_silos`, `santa_ursula`, `valle_gran_rey` | tribe_events | Tenerife, Tenerife, La Gomera | endpoint verified, calendar empty | none stated |

### HTML portals (`adapter: web`, 57 events)

| Source | Strategy | Result | Events stored |
|---|---|---|---|
| `webtenerife` | detail-jsonld (sitemap, 40 detail pages) | 40 / 40 / 9 | 7 (2 merged into Hola Islas events) |
| `auditorio_teatro_lpa` | embedded (Nuxt payload) | 24 / 24 / 3 | 3 |
| `teatro_leal` | blocks | 20 / 20 / 8 | 8 |
| `tea_tenerife` | blocks | 12 / 12 / 4 | 4 |
| `adeje_agenda` | blocks | 12 / 12 / 4 | 4 |
| `arona_agenda` | blocks | 25 / 25 / 12 | 12 |
| `lpacultura` | blocks | 20 / 20 / 10 | 10 |
| `cact_lanzarote` | blocks | 7 / 7 / 3 | 3 |
| `cabildo_fuerteventura_agenda` | blocks | 10 / 10 / 6 | 6 |

All HTML sources: license unknown (ToS not checked), priority 40. By island (all 126 events):
Tenerife 67, Gran Canaria 27, Fuerteventura 12, Lanzarote 10, La Palma 4, La Gomera 4, El Hierro
2, La Graciosa 0. 40 events have a start time; 86 are date-only. Of the 57 HTML events, 36 have no
start time and 27 no venue name (see known issues).

Disabled HTML sources (kept in `sources.yaml`): `grancanaria_agenda` (static page worked, kept
disabled), `tenerife_es_eventos` and `auditorio_tenerife` (JavaScript or admin-ajax lists; need a
renderer, never tested live). Excluded and never bypassed: lanzarote.com and tomaticket (HTTP 403
or Cloudflare challenge); turismolanzarote (placeholder microdata dates).

`ticketmaster` is implemented and tested against the documented schema only, and was skipped in
this run (no key).

### Verified but not integrated

| Source | Why |
|---|---|
| `visitlapalma.es`, `lagomera.travel`, `elhierro.travel` | Same Drupal JSON:API as Hola Islas Canarias; already covered. |
| `gobcan_agenda_cultural`, `santacruz_agenda_cultural` (CKAN) | Stale datasets (2022 and 2019). Adapter ready if they revive. |
| `deportes_tenerife_ical`, `antigua_ical` | Disabled duplicates of their REST sources. |

### Excluded

| Platform | Reason |
|---|---|
| Eventbrite API, Meetup, Songkick, Bandsintown, Resident Advisor, Fever, Eventim, allevents, SeatGeek | No usable public API for this purpose (see `config/sources.yaml`). |
| Telde (no dates), Tegueste (401), Puerto del Rosario (403), Mogan (500), CAAM (no events) | Checked live on 2026-10-15. |

Social platforms: see [Social platforms](#social-platforms).

### Reliability over the recorded runs

All 19 attempted sources succeeded in all 4 runs recorded in the database (4 of 4, 100%): the
first run, an idempotency re-run, a `--force` run and a `--force --low-resource` run, all on
2026-10-15. Four runs on one day say little about long-term reliability; run `report --health`
over time.

## Legal and technical limits

- **Hola Islas Canarias**: terms of service not published (`aviso-legal` returned 404 on
  2026-10-15), so the license is **unknown**. Review before any redistribution.
- **HTML portals**: terms were not reviewed; only public pages allowed by `robots.txt` are read.
- **Cabildo de Tenerife CKAN**: CC-BY, **attribution required**.
- **Ticketmaster**: API terms (attribution and link back; no revenue use on the free key).
- Hola event images (header and gallery, exported as `image_url` and `extra.images`) and tags
  are captured as links to the source's own files; image rights stay with the source, so link
  to them and do not re-host.
- Hola dates are **date-only**. Tribe sites often declare a wrong timezone; non-IANA values are
  read as Canary wall time. `params.force_timezone` handles iCal feeds labelled `TZID=UTC`.
- The per-event `source_license` is exported; check it before reuse.

## Requirements and measured resources

Python >= 3.12, [uv](https://docs.astral.sh/uv/), network access, SQLite (bundled). No GPU, no
server. Measured on 2026-10-15 on macOS 26 (Darwin 25.5.0) Apple Silicon, `/usr/bin/time -l` on
the venv interpreter, home internet connection, 1 request per second per host:

| Run | Wall time | CPU (user + sys) | Peak RSS |
|---|---|---|---|
| `import-events --force` (19 sources) | 50.6 s | 3.6 s | 196 MiB (205,422,592 B) |
| `import-events --force --low-resource` | 45.3 s | 2.4 s | 115 MiB (120,963,072 B) |
| `discover-sources` (124 requests) | 213.1 s | 2.5 s | 68 MiB (71,467,008 B) |

Wall time is dominated by network politeness: `webtenerife` alone takes about 48 s (40 detail
pages at 1 request per second). Low-resource mode caps each source at 15 pages, so `webtenerife`
yielded 13 events instead of 40 (3 in the window instead of 9): use it for small hosts, not as the
regular mode. A second normal run took 51.1 s; the first run (cold cache) reported 54.1 s.
Only these three runs were measured; other machines will differ. The optional extras and the
Ollama model were not measured (see [Optional extras](#optional-extras)).

## Operating cost

**0 EUR in paid APIs.** No paid service is used. Real costs are electricity and the host that
runs the weekly job. One import run fetched 60 pages (42 of them for `webtenerife`) plus
robots.txt checks; the exact request count is not recorded. Ollama, if
you enable it, runs locally. With a Ticketmaster key about 16 calls per run, far below the free
5000/day.

## Testing

```bash
uv run pytest              # offline suite (live tests deselected)
uv run pytest -m live      # opt-in: hits the real endpoints of the enabled sources
uv run ruff check .
uv run mypy src tests
```

Tests are written first (TDD); the offline suite uses recorded fixtures and `respx`.

## Limitations and known issues

- Ticketmaster has **never been run against the real API** (no key available).
- Playwright, Crawl4AI and Ollama paths were **never run live** (fakes and fixtures only).
- Events that disappear from a feed are not deleted: they remain exported while their date is
  in the window.
- **Source date typos pass the overlap filter when they form a long range**: in the 2026-10-15
  run `Trail Pino Santo` (Hola Islas) has start `0026-12-06`, and four more Hola events have
  starts in 2016 or 2025 with ends in Dec 2026. They are exported as in-window events. 27 of
  the 126 events span more than 60 days (not counting the `0026` one).
- **HTML extraction misses some details that the page states.** Spot check of 6 HTML events
  against their pages (Teatro Leal, Auditorio Alfredo Kraus, Adeje, Cabildo de Fuerteventura,
  LPA Cultura, TEA): title, date and venue matched in all 6, but the start time was missing for 3
  (Teatro Leal 18:00, Adeje 21:00h, TEA 19:00) and the venue was missing for LPA Cultura (the
  page names a library). Nothing was invented; fields were left empty.
- Cross-source duplicates the system did not flag: `Un matrimonio sin filtros` (Teatro Leal) vs
  the webtenerife record of the same day (title similarity 65, below the 75 review floor).
- Low-resource runs record a truncated listing in the source history (webtenerife 13 events);
  it stayed under the 70% anomaly threshold (67.5% drop) so it was not quarantined.
- `discover-sources` is noisy: of 144 candidates, 100 are open-data datasets matched by words
  such as "actividades" (many are statistics, not agendas); 77 of its 124 requests were errors
  (probes of domains without an endpoint). Candidates are never enabled.
- DNS rebinding (residual risk): the hostname is resolved and validated, then resolved again by
  the HTTP stack at connect time.
- The La Graciosa / Lanzarote bounding boxes overlap near Punta Fariones, so coordinate-only
  island resolution is coarse there.
