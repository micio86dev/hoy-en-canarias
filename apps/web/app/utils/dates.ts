export const CANARY_TIME_ZONE = 'Atlantic/Canary'

export interface EventWhen {
  startAt: string | null
  endAt: string | null
  startDate: string | null
  endDate: string | null
  allDay: boolean
}

function dayFormatter(locale: string): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat(locale, {
    timeZone: CANARY_TIME_ZONE,
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  })
}

function timeFormatter(locale: string): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat(locale, {
    timeZone: CANARY_TIME_ZONE,
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  })
}

/** A date-only value is anchored at noon UTC so it stays on the same day in every zone. */
function anchor(day: string): Date {
  return new Date(`${day}T12:00:00Z`)
}

/** Human-readable event date in the Canary Islands calendar, localised. Never invents a time. */
export function formatEventWhen(when: EventWhen, locale: string): string {
  const days = dayFormatter(locale)
  if (when.startAt) {
    const start = new Date(when.startAt)
    const day = days.format(start)
    if (when.allDay) {
      return day
    }
    const times = timeFormatter(locale)
    const startTime = times.format(start)
    if (!when.endAt) {
      return `${day} · ${startTime}`
    }
    const end = new Date(when.endAt)
    const sameDay = days.format(end) === day
    return sameDay ? `${day} · ${startTime}–${times.format(end)}` : `${day} · ${startTime} – ${days.format(end)} ${times.format(end)}`
  }
  if (when.startDate) {
    const first = days.format(anchor(when.startDate))
    if (when.endDate && when.endDate !== when.startDate) {
      return `${first} – ${days.format(anchor(when.endDate))}`
    }
    return first
  }
  return ''
}
