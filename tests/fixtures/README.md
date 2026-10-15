# Test fixtures

Real captures (trimmed, taken 2026-10-15 with read-only GET requests):

- `holaislascanarias_events_page1.json` / `_page2.json`: Drupal JSON:API `node/event` with
  `include=field_t_islands,field_t_municipality,field_event_category` and sparse fieldsets.
  `_page2.json` is the real second page with `links.next` removed (so it is a last page).
  `holaislascanarias_events_longrunning.json`: events with absurd/long ranges (a `0026-12-06`
  typo in the source data, and a 2016 to 2026 range).
- `tribe_candelaria.json`, `tribe_candelaria_page2.json` (`next_rest_url` removed),
  `tribe_granadilla.json`, `deportestenerife_tribe.json`, `tribe_empty.json` (Los Silos, 0 events):
  The Events Calendar REST. Descriptions truncated to 600 chars, image `sizes` removed.
- `deportestenerife.ics`, `antigua.ics`: The Events Calendar iCal export, trimmed to a few VEVENTs.
- `ckan_package_show_tenerife_sports.json`, `tenerife_sports_events.json` (8 of 3276 events).
- `ckan_package_show_gobcan_agenda.json`, `gobcan_agenda_proxima.csv` (real, complete, 5 rows, stale).

SYNTHETIC (hand-written, marked as such inside the files; no verified real feed exists):

- `sample.rss`, `sample.atom`: event feeds using the RSS event module (`ev:startdate`).
- `ticketmaster_page.json`: follows the documented Discovery API v2 schema (`_synthetic` key).
