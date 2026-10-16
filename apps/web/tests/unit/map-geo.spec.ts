import { describe, expect, it } from 'vitest'
import {
  CANARY_DEFAULT_VIEW,
  boundsToBbox,
  hasValidCoordinates,
  pointsWithCoordinates,
} from '../../app/utils/map-geo'

describe('coordinates', () => {
  it('accepts real coordinates inside the world bounds', () => {
    expect(hasValidCoordinates(28.1, -16.7)).toBe(true)
  })

  it('rejects missing, non-finite and out-of-range values', () => {
    expect(hasValidCoordinates(null, -16.7)).toBe(false)
    expect(hasValidCoordinates(28.1, Number.NaN)).toBe(false)
    expect(hasValidCoordinates(95, 0)).toBe(false)
    expect(hasValidCoordinates(0, 181)).toBe(false)
  })

  it('never invents a position for a point without coordinates', () => {
    const points = pointsWithCoordinates([
      { slug: 'a', latitude: 28.1, longitude: -16.7 },
      { slug: 'b', latitude: null, longitude: null },
      { slug: 'c', latitude: 28.0, longitude: null },
    ])

    expect(points.map((point) => point.slug)).toEqual(['a'])
  })
})

describe('default map view', () => {
  it('covers all eight islands from La Graciosa in the north to El Hierro in the south-west', () => {
    const [[south, west], [north, east]] = CANARY_DEFAULT_VIEW.bounds

    // La Graciosa ~29.25 N, -13.5 W; El Hierro ~27.75 N, -18.0 W; Lanzarote ~29.0 N, -13.6 W.
    expect(north).toBeGreaterThanOrEqual(29.3)
    expect(south).toBeLessThanOrEqual(27.7)
    expect(west).toBeLessThanOrEqual(-18.0)
    expect(east).toBeGreaterThanOrEqual(-13.4)
  })
})

describe('bounds to API bbox', () => {
  it('formats minLon,minLat,maxLon,maxLat with six decimals at most', () => {
    expect(
      boundsToBbox({ south: 27.5, west: -18.2, north: 29.5, east: -13.3 }),
    ).toBe('-18.2,27.5,-13.3,29.5')
  })

  it('clamps to the world bounds so the API never rejects a map drawn past the edge', () => {
    expect(
      boundsToBbox({ south: -95, west: -190, north: 95, east: 190 }),
    ).toBe('-180,-90,180,90')
  })
})
