/**
 * Guest favourites: a list of event slugs kept only in this browser.
 * Nothing here reaches the server; there are no accounts and no identifiers.
 */

/** Versioned so a future format can live beside this one without a migration. */
export const FAVORITES_KEY = 'hoy:favorites:v1'

/** Upper bound on stored slugs, so localStorage cannot grow without limit. */
export const MAX_FAVORITES = 200

const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
const MAX_SLUG_LENGTH = 120

function isSlug(value: unknown): value is string {
  return typeof value === 'string' && value.length <= MAX_SLUG_LENGTH && SLUG_PATTERN.test(value)
}

/** Tolerant read: corrupted or foreign data yields an empty list instead of an error. */
export function parseFavorites(raw: string | null): string[] {
  if (!raw) return []
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return []
  }
  if (!Array.isArray(data)) return []
  const unique = [...new Set(data.filter(isSlug))]
  return unique.slice(0, MAX_FAVORITES)
}

export function serializeFavorites(slugs: string[]): string {
  return JSON.stringify(slugs.slice(0, MAX_FAVORITES))
}

/** Most recent first. Adding beyond the cap drops the oldest entry. */
export function toggleFavorite(slugs: string[], slug: string): string[] {
  if (slugs.includes(slug)) return slugs.filter((item) => item !== slug)
  return [slug, ...slugs].slice(0, MAX_FAVORITES)
}
