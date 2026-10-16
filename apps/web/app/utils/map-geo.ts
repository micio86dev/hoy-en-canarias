export interface GeoBounds {
  south: number
  west: number
  north: number
  east: number
}

export interface MapView {
  center: [number, number]
  zoom: number
  /** [[south, west], [north, east]] */
  bounds: [[number, number], [number, number]]
}

// Covers all eight islands: La Graciosa to the north, El Hierro to the south-west.
export const CANARY_DEFAULT_VIEW: MapView = {
  center: [28.25, -16.0],
  zoom: 7,
  bounds: [
    [27.5, -18.3],
    [29.6, -13.2],
  ],
}

export function hasValidCoordinates(latitude: number | null, longitude: number | null): boolean {
  return (
    latitude !== null &&
    longitude !== null &&
    Number.isFinite(latitude) &&
    Number.isFinite(longitude) &&
    latitude >= -90 &&
    latitude <= 90 &&
    longitude >= -180 &&
    longitude <= 180
  )
}

/** Keeps only points with a real position. Nothing is ever placed at a guessed location. */
export function pointsWithCoordinates<T extends { latitude: number | null; longitude: number | null }>(
  items: T[],
): Array<T & { latitude: number; longitude: number }> {
  return items.filter(
    (item): item is T & { latitude: number; longitude: number } =>
      hasValidCoordinates(item.latitude, item.longitude),
  )
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/** Map bounds as the API expects them: minLon,minLat,maxLon,maxLat, clamped to the world. */
export function boundsToBbox(bounds: GeoBounds): string {
  const round = (value: number): number => Number(value.toFixed(6))
  return [
    round(clamp(bounds.west, -180, 180)),
    round(clamp(bounds.south, -90, 90)),
    round(clamp(bounds.east, -180, 180)),
    round(clamp(bounds.north, -90, 90)),
  ].join(',')
}
