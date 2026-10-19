import type { EventDetail } from '../types/api'

export type SiteLocale = 'es' | 'en' | 'it'
export const SITE_LOCALES: readonly SiteLocale[] = ['es', 'en', 'it']
export const DEFAULT_LOCALE: SiteLocale = 'es'

const CANARY = 'Atlantic/Canary'
const EVENT_STATUS: Record<string, string> = {
  scheduled: 'https://schema.org/EventScheduled',
  cancelled: 'https://schema.org/EventCancelled',
  postponed: 'https://schema.org/EventPostponed',
  rescheduled: 'https://schema.org/EventRescheduled',
  moved_online: 'https://schema.org/EventMovedOnline',
}

/** Spanish is unprefixed; English and Italian carry their locale prefix. */
export function canonicalPath(locale: SiteLocale, path: string): string {
  return locale === DEFAULT_LOCALE ? path : `/${locale}${path}`
}

/** An instant written in the Canary Islands wall-clock time with its real UTC offset. */
export function canaryIso(instant: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: CANARY,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
    timeZoneName: 'longOffset',
  }).formatToParts(new Date(instant))
  const value = (type: string): string => parts.find((part) => part.type === type)?.value ?? '00'
  const offset = (parts.find((part) => part.type === 'timeZoneName')?.value ?? 'GMT').replace('GMT', '') || '+00:00'
  return `${value('year')}-${value('month')}-${value('day')}T${value('hour')}:${value('minute')}:${value('second')}${offset}`
}

interface JsonLdContext {
  siteUrl: string
  url: string
  now?: Date
}

/** The event's start instant; a date-only start counts through its last moment in the Canary Islands. */
function startInstant(event: EventDetail): number {
  if (event.start_at) return Date.parse(event.start_at)
  if (event.start_date) {
    // Noon UTC is never on the other side of a Canary daylight-saving switch, so its offset is the day's offset.
    const dayOffset = canaryIso(`${event.start_date}T12:00:00Z`).slice(-6)
    return Date.parse(`${event.start_date}T23:59:59${dayOffset}`)
  }
  return Number.NaN
}

/** Only web links may reach structured data; a javascript: or other scheme never does. */
function isWebUrl(value: string | null): value is string {
  if (!value) return false
  try {
    return ['http:', 'https:'].includes(new URL(value).protocol)
  } catch {
    return false
  }
}

/** Schema.org Event. Only fields the source actually provided are written. */
export function buildEventJsonLd(event: EventDetail, context: JsonLdContext): Record<string, unknown> {
  const startDate = event.start_at ? canaryIso(event.start_at) : (event.start_date ?? undefined)
  const endDate = event.end_at ? canaryIso(event.end_at) : (event.end_date ?? undefined)
  // Owner-approved default: an upcoming event with no status yet is scheduled. Past or undated stays unknown.
  const isUpcoming = startInstant(event) > (context.now ?? new Date()).getTime()
  const status = event.status === 'unknown' && isUpcoming ? 'scheduled' : event.status
  // Owner-approved default: without a named organizer, the venue hosts the event and stands in for it.
  const organizer = event.organizer_name ?? event.venue_name

  const address = {
    '@type': 'PostalAddress',
    streetAddress: event.address ?? undefined,
    addressLocality: event.municipality ?? undefined,
    addressRegion: event.island ?? undefined,
    addressCountry: 'ES',
  }
  const hasAddress = Boolean(event.address || event.municipality || event.island)
  const hasPoint = event.latitude !== null && event.longitude !== null
  const location = {
    '@type': 'Place',
    name: event.venue_name ?? event.municipality ?? event.island ?? undefined,
    address: hasAddress ? address : undefined,
    geo: hasPoint ? { '@type': 'GeoCoordinates', latitude: event.latitude, longitude: event.longitude } : undefined,
  }

  // An offer needs a known price; its link is the first ticket, booking or official page the source gave.
  const offerUrl = [event.ticket_url, event.booking_url, event.official_event_url].find(isWebUrl)
  const offerLink = offerUrl ? { url: offerUrl } : {}
  let offers: Record<string, unknown> | undefined
  if (event.is_free === true) {
    offers = { '@type': 'Offer', price: 0, ...(event.currency ? { priceCurrency: event.currency } : {}), ...offerLink }
  } else if (event.price_min !== null && event.currency) {
    offers = { '@type': 'Offer', price: event.price_min, priceCurrency: event.currency, ...offerLink }
  }

  return {
    '@context': 'https://schema.org',
    '@type': 'Event',
    name: event.title,
    description: event.description ?? undefined,
    url: context.url,
    startDate,
    endDate,
    eventStatus: EVENT_STATUS[status],
    location,
    image: isWebUrl(event.image_url) ? event.image_url : undefined,
    organizer: organizer
      ? {
          '@type': 'Organization',
          name: organizer,
          url: event.organizer_name && isWebUrl(event.organizer_url) ? event.organizer_url : undefined,
        }
      : undefined,
    offers,
  }
}

/** JSON that is safe inside a script tag: no markup characters can end the element. */
export function safeJsonLd(data: unknown): string {
  return JSON.stringify(data)
    .replace(/</g, '\\u003c')
    .replace(/>/g, '\\u003e')
    .replace(/&/g, '\\u0026')
}
