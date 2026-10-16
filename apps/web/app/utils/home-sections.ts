/** Upcoming events minus the ones already shown in the "today" section. */
export function upcomingWithoutToday<T extends { id: string }>(upcoming: T[], today: T[]): T[] {
  const shown = new Set(today.map((item) => item.id))
  return upcoming.filter((item) => !shown.has(item.id))
}

/** Keeps only complete rows, so the grid never ends with a lone card. */
export function completeRows<T>(items: T[], columns: number): T[] {
  if (items.length < columns) return items
  return items.slice(0, items.length - (items.length % columns))
}

/** Drops zero counts: an empty category is noise on the home page. */
export function visibleCounts<T extends { count: number }>(items: T[]): T[] {
  return items.filter((item) => item.count > 0)
}
