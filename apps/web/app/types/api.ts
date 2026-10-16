// Hand-written mirror of hoy_api.schemas (kept small on purpose; generate later if it grows).
export interface EventSummary {
  id: string
  slug: string
  title: string
  start_at: string | null
  end_at: string | null
  start_date: string | null
  end_date: string | null
  all_day: boolean
  timezone: string
  category: string
  island: string | null
  municipality: string | null
  venue_name: string | null
  is_free: boolean | null
  price_min: number | null
  price_max: number | null
  currency: string | null
  image_url: string | null
  status: string
  stale: boolean
  latitude: number | null
  longitude: number | null
  distance_km: number | null
}

export interface EventPage {
  items: EventSummary[]
  page: number
  limit: number
  total: number
}

export interface CountItem {
  key: string
  count: number
}

export interface CountList {
  items: CountItem[]
}
