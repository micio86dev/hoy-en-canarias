import { describe, expect, it } from 'vitest'
import { islandFromSegment, islandToSegment } from '../../app/utils/route-segments'

describe('island URL segments', () => {
  it('turns an island name into a lowercase, accent-free segment', () => {
    expect(islandToSegment('Gran Canaria')).toBe('gran-canaria')
    expect(islandToSegment('La Graciosa')).toBe('la-graciosa')
  })

  it('resolves a segment back to the canonical island name', () => {
    expect(islandFromSegment('gran-canaria')).toBe('Gran Canaria')
    expect(islandFromSegment('el-hierro')).toBe('El Hierro')
  })

  it('returns undefined for unknown or malformed segments', () => {
    expect(islandFromSegment('atlantis')).toBeUndefined()
    expect(islandFromSegment('Gran Canaria')).toBeUndefined()
    expect(islandFromSegment('../etc')).toBeUndefined()
  })
})
