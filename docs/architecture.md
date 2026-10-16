# Architecture

## Shape

```
canarias-events importer (SQLite, untouched)
        |  subprocess: canarias-events import-events --no-ai
        v
hoy worker ── publish ──> PostgreSQL 17 + pgvector ──> hoy-api (FastAPI, /api/v1)
                                                             ^
                                                             | read-only server proxy
browser ──> Nuxt 4 SSR (apps/web) ───────────────────────────┘
```

## Decisions

1. **The importer stays on SQLite and runs as its own process.** Its merge rules, quarantine and 1,382 tests are the asset. The API never imports it at request time. A publish step projects its output into PostgreSQL.
2. **PostgreSQL with pgvector from day one.** Full-text (`simple` configuration with `unaccent`), trigram matching and the vector extension are available without changing the stack. Embeddings are not written yet.
3. **Stable identity.** Each published event has a UUID, an importer id and a slug. The slug is assigned at first publish and never recalculated, so renaming an event upstream does not break shared links.
4. **Honest data.** Missing values stay null. Unknown islands and statuses are dropped rather than guessed. Currency, organiser and availability are never assumed in structured data.
5. **Single worker, advisory lock.** The daily job runs in one process; the lock makes a second instance safe rather than necessary.
6. **Read-only public API.** Validation lives in the Pydantic query model. Errors share one JSON shape and never echo submitted values. Security headers and an explicit CORS allow-list are on every response.
7. **The browser never reaches the API directly.** A Nitro proxy forwards GET requests to the configured API origin only, with a path allow-list and a timeout.
8. **Client-only map.** Leaflet and markercluster load on demand. Markers exist only for real coordinates.
9. **i18n by URL prefix.** Spanish is the default without a prefix; English and Italian are prefixed. Catalogues must have identical keys (a test enforces it).

## Data model

- `events`: one row per published event, with `importer_id` (unique), `slug` (unique), dates in `timestamptz` and local-day columns derived in queries, `status`, `stale`, and a generated `search_tsv`.
- `event_sources`: provenance links, one per importer source record.
- `import_runs`: the run ledger (see `operations.md`).

## Extension points

- **Semantic search.** The `vector` extension is installed by migration 0001. A future migration adds an `embedding vector(N)` column plus an index, and a separate `hoy_api` module computes and stores vectors. Public endpoints keep their shape; a hybrid query blends `ts_rank_cd` with cosine distance. No LLM sits on the request path.
- **Anonymous interest signals.** Deferred. They need rate limiting and a privacy review first.
- **Localised route names.** Today the English and Italian paths reuse the Spanish segments. Localising them is a routing change, not a data change.

## Known gaps

- The sitemap is generated from a single API call and is not paginated; this holds up to the 50,000-URL protocol limit.
- Lighthouse mobile performance is below the 90 target in this environment (see `performance.md`).
- One API replica is assumed for migrations.
