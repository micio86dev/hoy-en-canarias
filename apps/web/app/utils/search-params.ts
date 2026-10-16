import { CATEGORIES, ISLANDS, PRESETS, SORTS, type Category, type Island, type Preset, type Sort } from './constants'

export interface SearchFilters {
  q?: string
  island?: Island
  municipality?: string
  category?: Category
  preset?: Preset
  from?: string
  to?: string
  free?: boolean
  sort?: Sort
  page?: number
  limit?: number
}

type RouteValue = string | null | undefined | Array<string | null>

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/
const MAX_QUERY_CHARS = 120

function single(value: RouteValue): string | undefined {
  const raw = Array.isArray(value) ? value[0] : value
  const trimmed = typeof raw === 'string' ? raw.trim() : ''
  return trimmed === '' ? undefined : trimmed
}

function oneOf<T extends readonly string[]>(list: T, value: string | undefined): T[number] | undefined {
  return value !== undefined && (list as readonly string[]).includes(value) ? (value as T[number]) : undefined
}

function isoDay(value: string | undefined): string | undefined {
  if (value === undefined || !ISO_DAY.test(value)) {
    return undefined
  }
  const parsed = new Date(`${value}T00:00:00Z`)
  return Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value ? undefined : value
}

function positiveInt(value: string | undefined, max: number): number | undefined {
  if (value === undefined || !/^\d+$/.test(value)) {
    return undefined
  }
  const parsed = Number(value)
  return parsed >= 1 && parsed <= max ? parsed : undefined
}

/** Reads search state from the URL. Invalid or unknown values are dropped, never guessed. */
export function filtersFromQuery(query: Record<string, RouteValue>): SearchFilters {
  const from = isoDay(single(query.from))
  let to = isoDay(single(query.to))
  if (from !== undefined && to !== undefined && to < from) {
    to = undefined
  }
  const q = single(query.q)?.slice(0, MAX_QUERY_CHARS)
  const filters: SearchFilters = {
    q,
    island: oneOf(ISLANDS, single(query.island)),
    municipality: single(query.municipality)?.slice(0, 120),
    category: oneOf(CATEGORIES, single(query.category)),
    preset: oneOf(PRESETS, single(query.preset)),
    from,
    to,
    free: single(query.free) === 'true' ? true : undefined,
    sort: oneOf(SORTS, single(query.sort)),
    page: positiveInt(single(query.page), 10_000),
    limit: positiveInt(single(query.limit), 50),
  }
  return Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== undefined)) as SearchFilters
}

/** Builds the API query string. Order is fixed so shared links and cache keys stay stable. */
export function filtersToApiQuery(filters: SearchFilters): URLSearchParams {
  const params = new URLSearchParams()
  const island = oneOf(ISLANDS, filters.island)
  const category = oneOf(CATEGORIES, filters.category)
  const preset = oneOf(PRESETS, filters.preset)
  const sort = oneOf(SORTS, filters.sort)
  if (filters.q?.trim()) params.set('q', filters.q.trim().slice(0, MAX_QUERY_CHARS))
  if (island) params.set('island', island)
  if (filters.municipality?.trim()) params.set('municipality', filters.municipality.trim())
  if (category) params.set('category', category)
  if (preset) params.set('preset', preset)
  if (!preset && isoDay(filters.from)) params.set('from', filters.from as string)
  if (!preset && isoDay(filters.to)) params.set('to', filters.to as string)
  if (filters.free) params.set('free', 'true')
  if (sort) params.set('sort', sort)
  if (filters.page && filters.page > 1) params.set('page', String(filters.page))
  if (filters.limit) params.set('limit', String(filters.limit))
  return params
}

/** The same parameters as the API, as a plain object for router queries. */
export function filtersToRouteQuery(filters: SearchFilters): Record<string, string> {
  return Object.fromEntries(filtersToApiQuery(filters))
}
