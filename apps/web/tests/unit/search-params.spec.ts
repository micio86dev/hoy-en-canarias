import { describe, expect, it } from 'vitest'
import { filtersFromQuery, filtersToApiQuery } from '../../app/utils/search-params'

describe('search URL state', () => {
  it('reads known filters and ignores invalid values', () => {
    const filters = filtersFromQuery({
      q: '  jazz  ',
      island: 'Tenerife',
      free: 'true',
      from: 'not-a-date',
      sort: 'chaos',
      page: '2',
      category: 'music',
    })

    expect(filters).toEqual({
      q: 'jazz',
      island: 'Tenerife',
      free: true,
      category: 'music',
      page: 2,
    })
  })

  it('drops a range whose end is before its start', () => {
    const filters = filtersFromQuery({ from: '2026-10-20', to: '2026-10-10' })

    expect(filters.from).toBe('2026-10-20')
    expect(filters.to).toBeUndefined()
  })

  it('maps UI filters to the API query parameter names, skipping unset ones', () => {
    const query = filtersToApiQuery({ q: 'jazz', preset: 'weekend', free: true, page: 2, limit: 20 })

    expect(query.toString()).toBe('q=jazz&preset=weekend&free=true&page=2&limit=20')
  })

  it('never sends unknown categories or islands to the API', () => {
    const query = filtersToApiQuery({ island: 'Atlantis' as never, category: 'nope' as never })

    expect(query.toString()).toBe('')
  })
})
