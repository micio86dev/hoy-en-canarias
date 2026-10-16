import { FAVORITES_KEY, parseFavorites, serializeFavorites, toggleFavorite } from '~/utils/favorites'

/**
 * Guest favourites shared by every component on the page.
 * Storage is this browser only; every read and write tolerates blocked or missing localStorage.
 */
export function useFavorites() {
  const slugs = useState<string[]>('favorites', () => [])
  const loaded = useState<boolean>('favorites-loaded', () => false)

  // Read after mount only, so server and browser render the same first markup.
  function load(): void {
    if (loaded.value || !import.meta.client) return
    try {
      slugs.value = parseFavorites(window.localStorage.getItem(FAVORITES_KEY))
    } catch {
      slugs.value = []
    }
    loaded.value = true
  }

  function persist(next: string[]): void {
    try {
      window.localStorage.setItem(FAVORITES_KEY, serializeFavorites(next))
    } catch {
      // Storage is blocked: the list still works for this visit.
    }
  }

  function isFavorite(slug: string): boolean {
    return slugs.value.includes(slug)
  }

  function toggle(slug: string): void {
    slugs.value = toggleFavorite(slugs.value, slug)
    persist(slugs.value)
  }

  return { slugs: readonly(slugs), load, isFavorite, toggle }
}
