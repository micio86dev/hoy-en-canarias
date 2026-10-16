// Mirrors the backend contract: keep in sync with canarias_events.geo.ISLANDS and
// hoy_api.publish.categories.CATEGORY_KEYS.
export const ISLANDS = [
  'Tenerife',
  'Gran Canaria',
  'Lanzarote',
  'Fuerteventura',
  'La Palma',
  'La Gomera',
  'El Hierro',
  'La Graciosa',
] as const

export const CATEGORIES = [
  'nightlife',
  'music',
  'markets',
  'sports',
  'family',
  'workshops',
  'nature',
  'culture',
  'festivals',
  'other',
] as const

export const PRESETS = ['today', 'tomorrow', 'weekend'] as const
export const SORTS = ['relevance', 'date', 'distance'] as const

export type Island = (typeof ISLANDS)[number]
export type Category = (typeof CATEGORIES)[number]
export type Preset = (typeof PRESETS)[number]
export type Sort = (typeof SORTS)[number]
