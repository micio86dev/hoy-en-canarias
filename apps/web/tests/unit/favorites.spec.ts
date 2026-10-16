import { describe, expect, it } from 'vitest'
import {
  FAVORITES_KEY,
  MAX_FAVORITES,
  parseFavorites,
  serializeFavorites,
  toggleFavorite,
} from '../../app/utils/favorites'

describe('favorites storage format', () => {
  it('uses a versioned key so a future format can coexist', () => {
    expect(FAVORITES_KEY).toBe('hoy:favorites:v1')
  })

  it('reads nothing as an empty list', () => {
    expect(parseFavorites(null)).toEqual([])
    expect(parseFavorites('')).toEqual([])
  })

  it('ignores corrupted or foreign data instead of throwing', () => {
    expect(parseFavorites('{not json')).toEqual([])
    expect(parseFavorites('{"slug":"x"}')).toEqual([])
    expect(parseFavorites('42')).toEqual([])
  })

  it('keeps only well-formed slugs, without duplicates', () => {
    const raw = JSON.stringify(['jazz-bc4511', 'jazz-bc4511', 'Bad Slug', '../etc', 42, '', 'mercado-2'])
    expect(parseFavorites(raw)).toEqual(['jazz-bc4511', 'mercado-2'])
  })

  it('round-trips through serialize and parse', () => {
    const slugs = ['jazz-bc4511', 'mercado-2']
    expect(parseFavorites(serializeFavorites(slugs))).toEqual(slugs)
  })

  it('caps the stored list so localStorage cannot grow without bound', () => {
    const many = Array.from({ length: MAX_FAVORITES + 25 }, (_, i) => `evento-${i}`)
    expect(parseFavorites(JSON.stringify(many))).toHaveLength(MAX_FAVORITES)
  })
})

describe('toggleFavorite', () => {
  it('adds a slug to the front, so the most recent favourite comes first', () => {
    expect(toggleFavorite(['a'], 'b')).toEqual(['b', 'a'])
  })

  it('removes a slug that is already a favourite', () => {
    expect(toggleFavorite(['b', 'a'], 'b')).toEqual(['a'])
  })

  it('does not mutate the input list', () => {
    const input = ['a']
    toggleFavorite(input, 'b')
    expect(input).toEqual(['a'])
  })

  it('drops the oldest favourite when the cap is reached', () => {
    const full = Array.from({ length: MAX_FAVORITES }, (_, i) => `evento-${i}`)
    const next = toggleFavorite(full, 'nuevo')
    expect(next).toHaveLength(MAX_FAVORITES)
    expect(next[0]).toBe('nuevo')
    expect(next).not.toContain(`evento-${MAX_FAVORITES - 1}`)
  })
})
