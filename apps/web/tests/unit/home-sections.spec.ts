import { describe, expect, it } from 'vitest'
import { upcomingWithoutToday, completeRows, visibleCounts } from '../../app/utils/home-sections'

const ev = (id: string) => ({ id })

describe('home sections', () => {
  it('removes events already shown in the today section', () => {
    const rest = upcomingWithoutToday([ev('a'), ev('b'), ev('c')], [ev('a')])
    expect(rest.map((e) => e.id)).toEqual(['b', 'c'])
  })

  it('trims to complete rows of three so no card sits alone', () => {
    expect(completeRows([ev('a'), ev('b'), ev('c'), ev('d')], 3).length).toBe(3)
    expect(completeRows([ev('a'), ev('b')], 3).length).toBe(2)
  })

  it('hides zero counts so empty categories are not shown as noise', () => {
    expect(visibleCounts([{ key: 'music', count: 2 }, { key: 'sports', count: 0 }])).toEqual([
      { key: 'music', count: 2 },
    ])
  })
})
