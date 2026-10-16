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
- [ ] T3 E2E test for save, reload and the favourites page. Needs Docker (db, api) and the Playwright run; not run yet.
- [ ] T4 Commit the integration (T2) as `feat(web): guest favourites on cards, event page and /favoritos`.

## Checks
- Unit: `pnpm exec vitest run` (68 tests before T2 files were added; see the run record below).
- Typecheck: `pnpm typecheck` exit 0.
- Not run: E2E and accessibility suites (require the Docker stack).

## Progress
- Branch: `feat/guest-favorites` (from `feat/hoy-portal`).
- Commit `c4bea63` docs: Railway deployment status in README.
- Native review of the README candidate: approved, low risk, acknowledged, authority burned.

## Next step
Run T3 with the Docker stack, then commit T2 and T3 as separate work units.
