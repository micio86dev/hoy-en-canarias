# Canarias Events Importer

Python ETL that imports **public events of the Canary Islands** for the next Monday-Sunday week
(Atlantic/Canary time), deduplicates them deterministically, stores them in SQLite and exports
JSON, CSV and reports.

Hard constraints: **no AI**, **no HTML scraping**, **no paid APIs**. Only open data, official
feeds (Tribe REST, iCal, RSS), JSON:API and free API keys.

## Contents

[Install](#install) | [Run](#run) | [Next-week rule](#next-week-rule) | [Free keys](#free-api-keys) |
[Add a source](#add-a-source) | [Query data](#query-the-data) | [Reports](#reports) |
[Scheduling](#scheduling) | [Deduplication](#deduplication) | [Security](#security) |
[Source status](#source-status) | [Legal and technical limits](#legal-and-technical-limits) |
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
| `list-sources` | Configured sources with adapter, enabled flag, islands, license, status. |
| `discover-sources` | Search open-data catalogs and probe candidate domains. Never enables anything. |
| `report` | Print the summary of a stored import run (`--run-id`, default latest). |

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
```

Global option: `--log-level DEBUG|INFO|WARNING` (or `CANARIAS_LOG_LEVEL`). Other options:
`--config` (default `config/sources.yaml`). `--from` and `--to` must be given together.

Outputs (default `output/`, written atomically): `events.json`, `events.csv` (UTF-8 with BOM, for
Excel), `import-report.json`, `sources-report.json`; `discover-sources` writes
`discovery-report.json`. The database defaults to `data/events.sqlite` (or `$CANARIAS_DB_PATH`).
`import-events` exits with code 1 when no source succeeded.

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

Imports are idempotent: a second run on the same window reports 0 new and 0 updated.

## Deduplication

Deterministic, no AI. For each incoming event, in order:

1. Same `(source, source event id)`: update of a known record.
2. Same canonical official URL **and** same start date, from another source: same event.
3. Same start/end dates, title similarity (token-sort ratio, normalized titles) >= 92, agreeing
   location (same municipality, or coordinates within 300 m, or similar venue name) and
   compatible start time: merged.

Near misses (similarity 75-92, or >= 92 with a different location or start time) stay separate
and are recorded in `possible_duplicates`. Two events of the **same source** are never
auto-merged. When sources disagree on a field, the source with higher `priority` wins; the
provenance of each field is stored. The threshold is in `import-report.json` (`merge_threshold`).

## Security

- Only `http`/`https`; every request goes through `SafeHttpClient`.
- SSRF guard: private, loopback and link-local addresses are refused.
- Redirects are followed manually, each hop re-validated; response size is capped (10 MB
  default, 2 MB for discovery); retries with backoff; `429` respected; per-host rate limit.
- Secrets are masked: key/token query values are redacted in logs (including httpx logs) and the
  HTTP cache stores masked URLs only.
- No credentials in the repository: `.env` is git-ignored.

## Source status

Counts are from the real run of **2026-10-15** (window 19-25 Oct 2026, Atlantic/Canary), shown as
received / valid / in window / new / merged.

### Integrated and verified

| Source | Adapter | Islands | Result | License |
|---|---|---|---|---|
| `holaislascanarias` (Turismo de Canarias) | drupal_jsonapi | all 8 | 50 / 50 / 50 / 50 / 0 | unknown |
| `cabildo_tenerife_deportes` (open data) | ckan | Tenerife | 3276 / 3270 / 7 / 7 / 0 | CC-BY |
| `deportes_tenerife` | tribe_events | Tenerife | 3 / 3 / 3 / 0 / 3 (all merged into Cabildo events) | none stated |
| `candelaria` | tribe_events | Tenerife | 8 / 8 / 8 / 8 / - | none stated |
| `santiago_del_teide` | tribe_events | Tenerife | 2 / 2 / 2 / 2 / - | none stated |
| `granadilla_de_abona` | tribe_events | Tenerife | 1 / 1 / 1 / 1 / - | none stated |
| `antigua` | tribe_events | Fuerteventura | 1 / 1 / 1 / 1 / - | none stated |
| `los_silos`, `santa_ursula`, `valle_gran_rey` | tribe_events | Tenerife, Tenerife, La Gomera | endpoint verified, calendar empty (0 events) | none stated |

Run totals: **69 distinct events** in the window, 3 possible duplicates (same source, never
merged; e.g. Festival Starmus held on La Palma and Tenerife is 2 records). By island: Tenerife
32, Gran Canaria 14, Lanzarote 7, Fuerteventura 6, La Palma 4, La Gomera 4, El Hierro 2, La
Graciosa 0. 19 events have a time, 50 are all-day (Hola Islas Canarias is date-only).

`ticketmaster` is implemented and tested against the documented schema only, and was skipped in
this run (no key).

### Verified but not integrated

| Source | Why |
|---|---|
| `visitlapalma.es`, `lagomera.travel`, `elhierro.travel` | Same Drupal JSON:API `/jsonapi/node/event` as Hola Islas Canarias (4, 4 and 2 window events); all already in `holaislascanarias`. Subset, no added coverage. |
| `gobcan_agenda_cultural` (CKAN) | Stale: 5 events ending 2022-03, metadata modified 2025-03-06. Adapter ready if it revives. |
| `santacruz_agenda_cultural` (CKAN) | Stale: metadata 2019-05-21, 2019 records. Field map only inspected, not run end to end. |
| `deportes_tenerife_ical`, `antigua_ical` | Disabled duplicates of their REST sources; kept as fallback. |

### Candidates: HTML only (no API, feed or iCal found; scraping is out of scope)

webtenerife.com, grancanaria.com agenda, tenerife.es eventos, Auditorio de Tenerife, Auditorio
Alfredo Kraus, Teatro Perez Galdos, Teatro Leal, TEA Tenerife, Ayuntamientos de La Laguna, Arona,
Arrecife and Adeje. PredictHQ: free plan limited, terms not checked.

### Unreachable at check time (re-check with `discover-sources`)

Radio Insular (HTTP 503), Cabildo de Fuerteventura (HTTP 503), Ocio Canarias, Mueca, Festival
de Canarias.

### Excluded

| Platform | Reason |
|---|---|
| Eventbrite | Public event search API closed in 2019. |
| Meetup | OAuth client requires the paid Pro plan. |
| Songkick | No free API for this use; not a coverage source. |
| Bandsintown | API limited to artist-managed data; no area search. |
| Resident Advisor, Fever, Eventim, allevents | No public API. |
| SeatGeek | No coverage in Spain. |
| PredictHQ | Candidate only: free plan limited, terms not checked. |

Also excluded after a live check: Telde (events without dates), Tegueste (401), Puerto del
Rosario (403), lanzarote.com (403), Mogan (500), CAAM (no events). Full list with dates:
`config/sources.yaml`.

## Legal and technical limits

- **Hola Islas Canarias**: terms of service not published (`aviso-legal` returned 404 on
  2026-10-15), so the license is **unknown**. Review before any redistribution.
- **Deportes Tenerife and municipal sites**: no explicit license stated.
- **Cabildo de Tenerife CKAN**: CC-BY, **attribution required**.
- **Ticketmaster**: API terms (attribution and link back; no revenue use on the free key).
- Hola dates are **date-only** (no times). Tribe sites often declare a wrong timezone
  (`Europe/London`, `UTC+0`): non-IANA values are read as Canary wall time. Some iCal feeds label
  local times as `TZID=UTC`; `params.force_timezone` handles it.
- Source typos exist (e.g. start year `0026`); the central overlap filter drops them.
- Coverage gaps: Gran Canaria and Lanzarote municipal agendas are HTML-only; La Graciosa had 0
  events in the run.
- The per-event `source_license` is exported; check it before reuse.

## Operating cost

About **0 EUR**. Roughly 15-20 HTTP requests per run without `discover-sources` (a few MB
transferred, cached with ETag), local SQLite, no servers. With a Ticketmaster key about 16 calls
per run, far below the free 5000/day.

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
- Events that disappear from a feed are not deleted: they remain exported while their date is
  in the window.
- DNS rebinding (residual risk): the hostname is resolved and validated, then resolved again by
  the HTTP stack at connect time. Mitigated by talking only to a fixed list of configured hosts.
- The La Graciosa / Lanzarote bounding boxes overlap near Punta Fariones, so coordinate-only
  island resolution is coarse there.
