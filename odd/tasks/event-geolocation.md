# Feature: event-geolocation

Objective: give every exported event a map point when one can be derived honestly, so events can be shown on a map.
Problem: only holaislascanarias publishes coordinates (83 of 216 events in the 2026-10-12..25 test run); other sources give at most a venue name or a municipality, and their raw payloads carry no coordinates (checked).
Why: the user wants to show the events on a map.
Branch: feat/adaptive-crawling (follow-up). TDD strict (source: project AGENTS.md + session), runner `uv run pytest`; checks `uv run ruff check .`, `uv run ruff format --check src tests`, `uv run mypy src tests`.
Delivery: ask-on-risk; no remote.

## Constraints
- Never guess fields: `latitude`/`longitude` stay exactly what the source gave. A derived point lives in separate map fields with an explicit precision.
- No external geocoding at run time (no Nominatim, no paid API). Reference data is bundled with provenance.
- Precision order: `source` (event coordinates) > `venue` (known venue table) > `municipality` (municipality reference point). Otherwise no point.

## Reference data (fetched 2026-10-16)
- Municipality points: Wikidata P625 of the 88 Canary municipalities (CC0), one SPARQL query; all 88 matched `geo.MUNICIPALITIES` and fall in the right island box.
- Venues (Wikidata P625, CC0): Teatro Leal Q950052, TEA Q2403459, Auditorio Alfredo Kraus Q3629501, Teatro Perez Galdos Q206404, Auditorio de Tenerife Q28964, Cueva de los Verdes Q899513, Palmetum Q3892794. Others pending (Wikidata rate limited the lookups).

## Tasks
- [x] G1 Map point derivation (route: delegated direct, one writer; 2+ non-trivial files): bundled municipality points + `config/venues.yaml`, `locate(event)` with precision, map fields in events.json/events.csv and a new events.geojson; README section
- [x] G2 Real-run check: coverage per precision on the test DB, spot-check points

## Acceptance criteria
- Source coordinates are never altered; derived points always carry `precision` and `basis`.
- A venue match requires the municipality (when known) to agree.
- events.geojson is valid GeoJSON (lon, lat order) with precision in properties.

## Progress / evidence

### G1 (route: delegated direct, one writer; 2+ non-trivial files)
- Commit 0743a09 feat(geo): derive map points for exported events. New geo_points.py (88 Wikidata municipality points, venue table loader + validation, locate()), config/venues.yaml (7 venues), events.json `map`, events.csv map_* columns, events.geojson, README "Map points".
- RED: test collection failed with ModuleNotFoundError canarias_events.geo_points (expected). GREEN: 1352 passed; ruff, ruff format, mypy clean (writer report, parent re-ran pytest: 1352 passed).
- Known limitation (writer): a malformed venues.yaml raises inside export_all after the DB write; social registry degrades instead. Left as is.

### G2 (route: direct inline; real data)
- Re-run on the 2026-10-12..25 test DB (scratchpad/test2w): first export showed one source point in Ecuador (Hola "Mar de Isla", Las Palmas). Fix 3659897: source coordinates are used for the map only inside the Canaries and on the event's island (RED: 3 new tests failed; GREEN 1355 passed; ruff/format/mypy clean). Source latitude/longitude unchanged.
- Coverage after fix, 217 events: source 82, venue 37, municipality 82, none 16 (93% with a point); 0 points on the wrong island.
- The 16 without a point have no municipality and an unknown venue (Casa de la Cultura de Puerto del Rosario, Auditorio Infanta Leonor, Paraninfo ULL, Jameos del Agua, ...).

### Review follow-ups (route: direct inline)
- RDD review of 634624b..1cf3362 approved and acknowledged; warnings fixed in 958d6d3: a malformed venues.yaml now logs a warning and exports continue with an empty venue table; the venue validation test now exercises the coordinate check. 1356 passed; ruff/format/mypy clean.

## Next step
Optional: extend config/venues.yaml with verified coordinates for the remaining venues.
