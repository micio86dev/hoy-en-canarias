# Feature: event-media

Objective: capture every image and tag a source publishes, for a public events portal.
Problem: holaislascanarias (the main source, 84 of 217 events in the 2026-10-12..25 test run) gives an image for only 2 events: the adapter's JSON:API sparse fieldset excludes the media relationships.
Why: the user plans a portal; images matter most.
Branch: feat/adaptive-crawling (follow-up). TDD strict (source: project AGENTS.md + session), runner `uv run pytest`; checks `uv run ruff check .`, `uv run ruff format --check src tests`, `uv run mypy src tests`.

## Evidence (live, 2026-10-16)
- Extended query (include field_h_images[.field_media_image], field_h_image_gallery[.field_media_image], field_t_tags) returns 200 in 10.7 s for 50 events (312 KB).
- 50/50 events have a gallery media (media--list_image), 49 files resolved; 1 has a header image (media--header_images); 0 tags in this window.
- File URL: origin + file--file attributes.uri.url (e.g. /sites/default/files/events/romeria%20pino.jpg); alt/width/height in the relationship meta.
- No source publishes an organizer logo/avatar (Tribe organizer empty, webtenerife organizer placeholder, Hola only internal uid).
- Licence of Hola images not declared (aviso legal 404): link, do not re-host, until checked.

## Tasks
- [x] M1 Hola media + tags (route: delegated direct, one writer; adapter + tests + fixture): image_url from header image, else first gallery image; all images in extra.images [{url, alt, width, height, role}]; tags from field_t_tags names; missing file -> skipped, never guessed
- [x] M2 Real-run check: image coverage per source on the test DB
- [x] M3 Auditorio LPA images (route: direct inline; one constant + one test): embedded extractor reads main_image / touch_image

## Acceptance criteria
- No invented URLs: only file URLs present in the payload, made absolute against the source origin.
- Existing fields and dedup behaviour unchanged; image_url keeps the existing precedence when merged.

## Progress / evidence

### M1 (route: delegated direct, one writer; adapter + tests + fixture)
- Commit 0b2a3e4 feat(hola): capture event images and tags. Request includes header/gallery media + files + tags; image_url = header else first gallery; extra.images [{url, alt, width, height, role}]; tags from field_t_tags; missing files skipped. Exporter unchanged (extra already exported).
- RED: 6 of 21 adapter tests failed (no extra.images/tags; request lacked field_t_tags). GREEN: 1365 passed (parent re-ran); ruff, format, mypy clean.

### M2 (route: direct inline; real data)
- Re-import of holaislascanarias on the 2026-10-12..25 test DB: 9.7 s, 83 updated. Image coverage: Hola 84/86 (was 2/84); all other sources unchanged (Auditorio LPA 1/13 and Cabildo Tenerife deportes 5/9 are the remaining gaps).
- 6 random Hola image URLs: HTTP 200 with image/jpeg or image/png.

### Review follow-ups (route: direct inline)
- RDD review of 1cf3362..a209f5a approved and acknowledged. Warning R3-offhost-image-urls fixed: image URLs must share the source origin (absolute or protocol-relative paths to another host are skipped; RED 2 tests, GREEN 1367 passed). Venues warning test now asserts the WARNING record. Hola still 84/86 images after re-import.

### M3 (route: direct inline; one constant + one test)
- Cause: Auditorio productions carry their picture in main_image / touch_image (Nuxt media objects with fullUrl on api.auditorioteatrolaspalmasgc.es); IMAGE_KEYS only had image/image_url/imagen/thumbnail/cover_image, so only the carousel copy matched.
- RED: test_auditorio_productions_take_their_main_or_touch_image failed. GREEN: 1368 passed; ruff, format, mypy clean.
- Live: auditorio_teatro_lpa 13/13 window events with image (was 1/13); 3 sampled URLs HTTP 200 image/jpeg.

## Next step
Optional: Cabildo Tenerife deportes images (5/9).
