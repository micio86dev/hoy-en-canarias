import { ISLANDS, type Island } from './constants'

/** "Gran Canaria" -> "gran-canaria": stable, lowercase, accent-free URL segment. */
export function islandToSegment(island: string): string {
  return island
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
}

/** Resolves a URL segment back to the canonical island name, or undefined when unknown. */
export function islandFromSegment(segment: string): Island | undefined {
  return ISLANDS.find((island) => islandToSegment(island) === segment)
}
