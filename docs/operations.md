# Operations

## Services

| Service | What it does | Health |
| --- | --- | --- |
| `db` | PostgreSQL 17 with pgvector 0.8.1, named volume `pgdata` | `pg_isready` |
| `api` | FastAPI on port 8000. Runs `alembic upgrade head`, then serves | `GET /health` (liveness), `GET /ready` (database) |
| `worker` | `hoy worker`: the daily importer at 04:00 Atlantic/Canary | process running |
| `web` | Nuxt SSR on port 3000. Calls the API only through its server proxy | `GET /` |

Start everything: `docker compose up -d --build db api worker web`.
Run one importer cycle now: `docker compose exec worker hoy import-now`.

## Scheduling

- The worker computes the next 04:00 local instant from the Canary Islands calendar date, so daylight-saving changes move nothing by an hour.
- Each cycle imports the window from today to `HOY_IMPORT_WINDOW_DAYS` ahead (60 by default), then publishes the importer's SQLite output.
- A PostgreSQL session-level advisory lock makes overlapping runs impossible, across replicas too. A run that finds the lock held is recorded as `skipped_locked`.
- Retries are bounded by `HOY_IMPORT_MAX_ATTEMPTS` with a linear backoff. A timeout counts as a failed attempt.
- Every cycle writes one row to `import_runs`: trigger, status, attempts, window, duration, counts and a sanitised error. Database URLs and `apikey`/`token`-style query values are redacted before storage.
- Structured JSON logs go to stderr.

To check the last runs:

```sql
SELECT id, trigger, status, attempts, events_inserted, events_updated, events_stale, error_summary
FROM import_runs ORDER BY id DESC LIMIT 10;
```

## Manual import over HTTP

`POST /api/v1/admin/import` with `Authorization: Bearer <HOY_ADMIN_TOKEN>` returns `202` and runs a cycle in the background. Without a configured token the route answers `404`, and a wrong token gets `401`. Do not expose this endpoint without TLS.

## Migrations

- Migrations live in `apps/api/alembic/versions`. Apply with `hoy migrate` or `alembic upgrade head` from `apps/api`.
- Each revision is additive or explicitly reversible. The `api` container applies pending revisions on start, so run a single API replica until a dedicated migration job exists.
- Test migrations from a clean database with `pnpm test:e2e` (the preparation script resets `*_test` databases only).

## Backups and restore

Two stores hold state:

1. **PostgreSQL** (the published catalogue, provenance and run history)
   - Backup: `docker compose exec -T db pg_dump -U hoy -Fc hoy > hoy-$(date +%F).dump`
   - Restore into an empty database: `docker compose exec -T db pg_restore -U hoy -d hoy --clean --if-exists < hoy-YYYY-MM-DD.dump`
   - Restore drill: restore into `hoy_test` and run `pnpm test:e2e` against it before trusting the backup.
2. **Importer SQLite** (`importer-data` volume, `/data/events.sqlite`)
   - It is rebuilt by the next import, but it holds quarantine and source history. Back it up with a copy while no import runs: `docker run --rm -v hoy-en-canarias_importer-data:/data -v "$PWD":/backup alpine cp /data/events.sqlite /backup/`.
   - The published catalogue can be rebuilt from it with `hoy publish --sqlite <file>`.

Backups are not automated in this repository. Schedule them with the host's tooling.

## Failure behaviour

- An importer failure never deletes published events. Missing events are marked `stale` and keep being served with an honest label.
- An empty importer export does not mark the catalogue stale, because it usually means an upstream outage.
- If the database is unreachable, `/health` still answers and `/ready` returns `503` with no detail.

## Production checklist

- Set `HOY_DATABASE_URL`, `POSTGRES_PASSWORD`, `HOY_SITE_URL` and `NUXT_PUBLIC_SITE_URL` (the last one at build time). Never use the development passwords.
- Put TLS in front of `web` and `api`. The API trusts `X-Forwarded-*` only behind that proxy.
- Replace the default OpenStreetMap tile URL with a provider you contract for production traffic.
- Register the domain and Search Console yourself. This repository adds no verification tokens.
