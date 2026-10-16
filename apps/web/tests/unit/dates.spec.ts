import { describe, expect, it } from 'vitest'
import { formatEventWhen } from '../../app/utils/dates'

describe('event dates in the Canary Islands calendar', () => {
  it('shows the local time for a timed event (WEST, UTC+1 in October)', () => {
    const text = formatEventWhen(
      { startAt: '2026-10-21T20:00:00+00:00', endAt: null, startDate: null, endDate: null, allDay: false },
      'es',
    )

    expect(text).toContain('21:00')
  })

  it('omits the time for an all-day or date-only event', () => {
    const text = formatEventWhen(
      { startAt: null, endAt: null, startDate: '2026-10-19', endDate: null, allDay: true },
      'en',
    )

    expect(text).toMatch(/19/)
    expect(text).not.toMatch(/\d{1,2}:\d{2}/)
  })

  it('renders a multi-day range without a time', () => {
    const text = formatEventWhen(
      { startAt: null, endAt: null, startDate: '2026-10-17', endDate: '2026-10-18', allDay: true },
      'it',
    )

    expect(text).toMatch(/17.*18/)
  })
})
