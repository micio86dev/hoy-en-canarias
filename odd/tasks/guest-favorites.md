# Guest favourites

## Objective
Give visitors a reason to come back: save events to favourites in their own browser, with no account and nothing sent to a server.

## Why
Accounts are out of scope (README: "No accounts, no tracking cookies"). Favourites in localStorage keep that promise and still bring people back on the same device. The limit is that favourites do not sync between devices and are lost if the visitor clears site data.

## Scope
- `app/utils/favorites.ts`: pure parse, serialise and toggle (versioned key, slug validation, cap of 200).
- `app/composables/useFavorites.ts`: shared state; every storage access is guarded.
- `app/components/FavoriteButton.vue`: toggle with `aria-pressed`.
- `app/pages/favoritos.vue`: the saved list, `noindex`, counts events that no longer exist instead of inventing them.
- Button on `EventCard` and on the event page; `nav.favorites` in the header.
- Strings in es, en, it.

## Out of scope
Accounts, sync, email alerts, server storage, tracking of any kind.

## Tasks
- [x] T1 Storage format tests written first (RED confirmed: module missing) and implementation (GREEN, 10 tests). Commit `6f26faa`.
- [x] T2 Composable, button, page, i18n strings, integration in card, header and event page. Typecheck clean; unit suite green.
- [x] T3 E2E for save, reload, removal and noindex (`tests/e2e/favorites.spec.ts`). Full suite: 46 passed, 4 skipped (visual captures), on desktop and mobile. One earlier run failed on the existing filter test and passed on rerun in isolation and in a second full run; not reproduced.
- [x] T4 Integration committed (`fde2c34`). Bug found by T3 fixed: removing a favourite on /favoritos kept the card until reload.

## Checks
- Unit: `pnpm exec vitest run`: 12 files, 68 tests passing (T2 added no new tests, so the count is unchanged).
- Typecheck: `pnpm typecheck` exit 0.
- E2E: `docker compose --profile test up -d db_test`, `pnpm build`, `pnpm test:e2e`: 46 passed, 4 skipped.

## Progress
- Branch: `feat/guest-favorites` (from `feat/hoy-portal`).
- Commit `c4bea63` docs: Railway deployment status in README.
- Native review of the README candidate: approved, low risk, acknowledged, authority burned.

## Next step
Commit the E2E spec and the page fix, then the review gate for the committed range.
