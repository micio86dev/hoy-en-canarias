# Hoy en Canarias

Public, multilingual (es, en, it) discovery of events across the eight Canary Islands. Built on a
Python importer that collects public event sources into one catalogue, a FastAPI read API, and a
Nuxt 4 site with server-rendered pages, search, a map and SEO.

Status: deployed on Railway (project `hoy-en-canarias`: `web`, `api`, `worker`, `postgres`). Served
on the generated `*.up.railway.app` domain. No custom domain is registered yet.

## Quick start (Docker)

```bash
cp .env.example .env            # then set real passwords and HOY_SITE_URL
docker compose up -d --build db api worker web
```

- Site: http://localhost:3000 (Spanish; `/en/` and `/it/` for the other languages)
- API: http://localhost:8000/health and `/api/v1/events`
- The daily importer runs at 04:00 Atlantic/Canary in the `worker` container. Run one now with
  `docker compose exec worker hoy import-now`.

The database starts empty. Load the catalogue with the importer: `docker compose exec worker hoy import-now`
publishes the window's events into PostgreSQL on its own run.

## Local development

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 24 LTS with pnpm 11, Docker.

```bash
uv sync --all-packages                          # importer + API
docker compose up -d db                         # PostgreSQL + pgvector
cd apps/api && uv run hoy migrate               # apply migrations
uv run --package hoy-api uvicorn hoy_api.main:app --reload --port 8000

cd apps/web && pnpm install && NUXT_API_BASE=http://localhost:8000 pnpm dev
```

## Repository layout

| Path | Contents |
| --- | --- |
| `src/canarias_events/` | The importer: source adapters, deduplication, quarantine, exports. Unchanged by the portal. |
| `apps/api/` | FastAPI API, Postgres models, Alembic migrations, publish projection, worker, `hoy` CLI |
| `apps/web/` | Nuxt 4 site: pages, components, i18n catalogues, server proxy, sitemap and robots routes |
| `config/` | Importer configuration (sources, social registry, venue points) |
| `docs/` | Architecture, operations, performance and the importer reference |
| `docker-compose.yml` | db, api, worker, web; `db_test` runs under the `test` profile |

## Tests

| Suite | Command | Last result |
| --- | --- | --- |
| Importer | `uv run pytest` | 1382 passed, 20 deselected (live network) |
| API (incl. PostgreSQL integration) | `cd apps/api && uv run pytest` | 108 passed |
| Web unit | `cd apps/web && pnpm test` | 49 passed |
| End-to-end + accessibility (Playwright) | `docker compose --profile test up -d db_test && cd apps/web && pnpm test:e2e` | 34 passed |
| Types and lint | `cd apps/api && uv run mypy && uv run ruff check .`, `cd apps/web && pnpm typecheck` | clean |

The E2E suite resets a disposable `hoy_test` database, starts the API and the production build,
and runs desktop and mobile projects. Details and measurements: `docs/performance.md`.

## Key behaviour

- **Honest data.** Missing values stay empty. Unknown islands or statuses are dropped. Structured
  data never assumes a currency, organiser or availability. Events are never deleted when a source
  fails; they are marked stale.
- **Dates in the Canary calendar.** All day windows and displayed times use Atlantic/Canary.
- **Discovery without registration.** No accounts, no tracking cookies. Geolocation runs only after
  a tap and is not stored. Voice search is optional and uses the browser's own recognition.
- **SEO.** Canonical and hreflang per locale, Event JSON-LD built only from provided fields, a
  multilingual sitemap, robots and llms.txt. Filtered search results are `noindex`.

## Configuration

See `.env.example` for every variable. Two are build-time: `NUXT_PUBLIC_SITE_URL` (canonical and
hreflang URLs) and the importer's source keys. The map tile server is `NUXT_PUBLIC_TILE_URL`. The
default OpenStreetMap server is for development only; production needs a provider you contract.

## Documentation

- [Architecture and decisions](docs/architecture.md)
- [Operations: scheduling, migrations, backups, production checklist](docs/operations.md)
- [Performance and quality measurements](docs/performance.md)
- [Importer reference (sources, scheduling, deduplication)](docs/importer.md)

## Known limitations

- Mobile Lighthouse performance is 72–83 against a target of 90 (desktop meets it). The diagnosis
  and next steps are in `docs/performance.md`.
- No custom domain yet; the site runs on a Railway-generated domain. Backup scheduling and
  monitoring are not configured.
- Google Search Console and other verification tokens are not added; they must come from your
  own accounts.
- Semantic search is prepared (pgvector is installed) but not implemented.
- Interest signals ("Me interesa") are deferred for privacy review.
- Route names are shared across languages; localised paths are not done yet.
- A single API replica is assumed, because migrations run on container start.
