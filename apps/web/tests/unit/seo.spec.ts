import { describe, expect, it } from 'vitest'
import { buildEventJsonLd, canonicalPath, safeJsonLd } from '../../app/utils/seo'
import { buildSitemapXml } from '../../app/utils/sitemap'
import type { EventDetail } from '../../app/types/api'

const SITE = 'https://hoyencanarias.es'

function detail(overrides: Partial<EventDetail> = {}): EventDetail {
  return {
    id: '1',
    slug: 'concierto-de-jazz-bc4511',
    title: 'Concierto de jazz en el puerto',
    start_at: '2026-10-21T20:00:00Z',
    end_at: '2026-10-21T22:00:00Z',
    start_date: '2026-10-21',
    end_date: null,
    all_day: false,
    timezone: 'Atlantic/Canary',
    category: 'music',
    island: 'Tenerife',
    municipality: 'Adeje',
    venue_name: 'Puerto',
    is_free: null,
    price_min: null,
    price_max: null,
    currency: null,
    image_url: null,
    status: 'unknown',
    stale: false,
    latitude: null,
    longitude: null,
    distance_km: null,
    description: 'Jazz al atardecer.',
    address: null,
    tags: [],
    booking_url: null,
    ticket_url: null,
    official_event_url: null,
    organizer_name: null,
    organizer_url: null,
    first_seen_at: null,
    last_seen_at: null,
    last_verified_at: null,
    updated_at: '2026-10-16T03:00:00Z',
    sources: [],
    ...overrides,
  }
}

describe('canonical paths', () => {
  it('leaves Spanish unprefixed and prefixes English and Italian', () => {
    expect(canonicalPath('es', '/evento/x')).toBe('/evento/x')
    expect(canonicalPath('en', '/evento/x')).toBe('/en/evento/x')
    expect(canonicalPath('it', '/')).toBe('/it/')
  })
})

describe('Event JSON-LD', () => {
  it('writes start times in the Canary Islands offset', () => {
    const data = buildEventJsonLd(detail(), { siteUrl: SITE, url: `${SITE}/evento/x` })

    expect(data.startDate).toBe('2026-10-21T21:00:00+01:00')
    expect(data.endDate).toBe('2026-10-21T23:00:00+01:00')
  })

  it('uses a date-only value when no time is known', () => {
    const data = buildEventJsonLd(detail({ start_at: null, end_at: null }), { siteUrl: SITE, url: SITE })

    expect(data.startDate).toBe('2026-10-21')
    expect(data.endDate).toBeUndefined()
  })

  it('omits offers when the source gave no price', () => {
    const data = buildEventJsonLd(detail(), { siteUrl: SITE, url: SITE })

    expect(data.offers).toBeUndefined()
  })

  it('marks an upcoming event with unknown status as scheduled', () => {
    const data = buildEventJsonLd(detail(), { siteUrl: SITE, url: SITE, now: new Date('2026-10-19T12:00:00Z') })

    expect(data.eventStatus).toBe('https://schema.org/EventScheduled')
  })

  it('marks an upcoming date-only event with unknown status as scheduled', () => {
    const data = buildEventJsonLd(detail({ start_at: null, end_at: null }), {
      siteUrl: SITE,
      url: SITE,
      now: new Date('2026-10-19T12:00:00Z'),
    })

    expect(data.eventStatus).toBe('https://schema.org/EventScheduled')
  })

  it('uses the winter offset for a date-only event in January', () => {
    // Canary is UTC+0 in winter: 23:30 UTC on the 15th is still the 15th, so the event is upcoming.
    const data = buildEventJsonLd(detail({ start_at: null, end_at: null, start_date: '2026-01-15' }), {
      siteUrl: SITE,
      url: SITE,
      now: new Date('2026-01-15T23:30:00Z'),
    })

    expect(data.eventStatus).toBe('https://schema.org/EventScheduled')
  })

  it('leaves the status off a past event whose status is unknown', () => {
    const data = buildEventJsonLd(detail(), { siteUrl: SITE, url: SITE, now: new Date('2026-11-01T12:00:00Z') })

    expect(data.eventStatus).toBeUndefined()
  })

  it('leaves the status off when no start is known', () => {
    const data = buildEventJsonLd(detail({ start_at: null, start_date: null }), {
      siteUrl: SITE,
      url: SITE,
      now: new Date('2026-10-19T12:00:00Z'),
    })

    expect(data.eventStatus).toBeUndefined()
  })

  it('keeps a known cancellation even for a past event', () => {
    const data = buildEventJsonLd(detail({ status: 'cancelled' }), {
      siteUrl: SITE,
      url: SITE,
      now: new Date('2026-11-01T12:00:00Z'),
    })

    expect(data.eventStatus).toBe('https://schema.org/EventCancelled')
  })

  it('names the venue as organizer when the source names none', () => {
    const data = buildEventJsonLd(detail({ venue_name: 'Puerto', organizer_name: null }), { siteUrl: SITE, url: SITE })

    expect(data.organizer).toEqual({ '@type': 'Organization', name: 'Puerto' })
  })

  it('prefers the organizer the source named over the venue', () => {
    const data = buildEventJsonLd(detail({ venue_name: 'Puerto', organizer_name: 'Ayuntamiento' }), {
      siteUrl: SITE,
      url: SITE,
    })

    expect(data.organizer).toMatchObject({ name: 'Ayuntamiento' })
  })

  it('omits the organizer when neither the source nor a venue names one', () => {
    const data = buildEventJsonLd(detail({ venue_name: null, organizer_name: null }), { siteUrl: SITE, url: SITE })

    expect(data.organizer).toBeUndefined()
  })

  it('declares a free offer only when the source says the event is free', () => {
    const data = buildEventJsonLd(detail({ is_free: true, currency: 'EUR' }), { siteUrl: SITE, url: SITE })

    expect(data.offers).toEqual({
      '@type': 'Offer',
      price: 0,
      priceCurrency: 'EUR',
    })
  })

  it('never asserts an attendance mode or availability the source did not give', () => {
    const data = buildEventJsonLd(detail({ is_free: true, currency: 'EUR' }), { siteUrl: SITE, url: SITE })

    expect(data.eventAttendanceMode).toBeUndefined()
    expect((data.offers as Record<string, unknown>).availability).toBeUndefined()
  })

  it('never assumes a currency: a free offer carries no currency code unless the source gave one', () => {
    const data = buildEventJsonLd(detail({ is_free: true, currency: null }), { siteUrl: SITE, url: SITE })

    expect(data.offers).toEqual({ '@type': 'Offer', price: 0 })
  })

  it('omits a paid offer whose currency is unknown rather than guessing EUR', () => {
    const data = buildEventJsonLd(detail({ price_min: 5, currency: null }), { siteUrl: SITE, url: SITE })

    expect(data.offers).toBeUndefined()
  })

  it('points a priced offer at the ticket link the source gave', () => {
    const data = buildEventJsonLd(
      detail({ price_min: 5, currency: 'EUR', ticket_url: 'https://tickets.example/a' }),
      { siteUrl: SITE, url: SITE },
    )

    expect(data.offers).toEqual({
      '@type': 'Offer',
      price: 5,
      priceCurrency: 'EUR',
      url: 'https://tickets.example/a',
    })
  })

  it('falls back to the booking link, then the official page, for the offer url', () => {
    const booking = buildEventJsonLd(
      detail({ is_free: true, currency: 'EUR', booking_url: 'https://book.example/b', official_event_url: 'https://official.example/o' }),
      { siteUrl: SITE, url: SITE },
    )
    const official = buildEventJsonLd(
      detail({ is_free: true, currency: 'EUR', official_event_url: 'https://official.example/o' }),
      { siteUrl: SITE, url: SITE },
    )

    expect(booking.offers).toMatchObject({ url: 'https://book.example/b' })
    expect(official.offers).toMatchObject({ url: 'https://official.example/o' })
  })

  it('never writes an offer url when the source gave no link', () => {
    const data = buildEventJsonLd(detail({ price_min: 5, currency: 'EUR' }), { siteUrl: SITE, url: SITE })

    expect(data.offers).not.toHaveProperty('url')
  })

  it('never writes an image or organizer link that is not http or https', () => {
    const data = buildEventJsonLd(
      detail({ image_url: 'javascript:alert(1)', organizer_name: 'Ayuntamiento', organizer_url: 'data:text/html,x' }),
      { siteUrl: SITE, url: SITE },
    )

    expect(data.image).toBeUndefined()
    expect(data.organizer).toEqual({ '@type': 'Organization', name: 'Ayuntamiento', url: undefined })
  })

  it('never writes a link that is not http or https', () => {
    const data = buildEventJsonLd(
      detail({ price_min: 5, currency: 'EUR', ticket_url: 'javascript:alert(1)' }),
      { siteUrl: SITE, url: SITE },
    )

    expect(data.offers).not.toHaveProperty('url')
  })

  it('writes no offer at all for a link without a known price', () => {
    const data = buildEventJsonLd(detail({ ticket_url: 'https://tickets.example/a' }), { siteUrl: SITE, url: SITE })

    expect(data.offers).toBeUndefined()
  })

  it('maps a known cancellation to schema.org', () => {
    const data = buildEventJsonLd(detail({ status: 'cancelled' }), { siteUrl: SITE, url: SITE })

    expect(data.eventStatus).toBe('https://schema.org/EventCancelled')
  })

  it('includes a geo point only with real coordinates', () => {
    const withPoint = buildEventJsonLd(detail({ latitude: 28.1, longitude: -16.7 }), { siteUrl: SITE, url: SITE })
    const without = buildEventJsonLd(detail(), { siteUrl: SITE, url: SITE })

    expect(withPoint.location).toMatchObject({ geo: { latitude: 28.1, longitude: -16.7 } })
    expect((without.location as { geo?: unknown }).geo).toBeUndefined()
  })

  it('escapes markup so the JSON cannot close its script tag', () => {
    const payload = safeJsonLd({ name: '</script><script>alert(1)</script>' })

    expect(payload).not.toContain('</script')
    expect(JSON.parse(payload)).toEqual({ name: '</script><script>alert(1)</script>' })
  })
})

describe('sitemap XML', () => {
  it('lists each URL with its alternates and escapes XML', () => {
    const xml = buildSitemapXml(SITE, [
      { path: '/evento/a&b', lastmod: '2026-10-16T03:00:00Z' },
    ])

    expect(xml).toContain('<urlset')
    expect(xml).toContain('xmlns:xhtml="http://www.w3.org/1999/xhtml"')
    expect(xml).toContain(`<loc>${SITE}/evento/a&amp;b</loc>`)
    expect(xml).toContain(`hreflang="en" href="${SITE}/en/evento/a&amp;b"`)
    expect(xml).toContain(`hreflang="it" href="${SITE}/it/evento/a&amp;b"`)
    expect(xml).toContain('<lastmod>2026-10-16T03:00:00Z</lastmod>')
  })
})

describe('sitemap lastmod', () => {
  it('omits lastmod for pages without a known modification date', () => {
    const xml = buildSitemapXml(SITE, [{ path: '/buscar' }])

    expect(xml).not.toContain('<lastmod>')
    expect(xml).toContain(`<loc>${SITE}/buscar</loc>`)
  })
})

