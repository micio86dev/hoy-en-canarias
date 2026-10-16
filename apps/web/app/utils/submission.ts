import { ISLANDS, type Island } from './constants'

export interface SubmissionDraft {
  title: string
  description: string
  startDate: string
  island: string
  municipality: string
  venue: string
  url: string
  honeypot: string
}

export interface SubmissionPayload {
  title: string
  description: string
  start_date: string
  island: Island
  municipality?: string
  venue?: string
  url?: string
  honeypot: string
}

export interface SubmissionResult {
  errors: Partial<Record<'title' | 'startDate' | 'island' | 'url', string>>
  payload: SubmissionPayload | null
}

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/
const MAX_AHEAD_DAYS = 366

function isPlausibleDay(value: string, today: Date): boolean {
  if (!ISO_DAY.test(value)) return false
  const day = new Date(`${value}T00:00:00Z`)
  if (Number.isNaN(day.getTime()) || day.toISOString().slice(0, 10) !== value) return false
  // "Today" is the Canary Islands calendar day, the same rule the API applies.
  const canaryToday = new Intl.DateTimeFormat('en-CA', { timeZone: 'Atlantic/Canary' }).format(today)
  const start = new Date(`${canaryToday}T00:00:00Z`)
  const ahead = (day.getTime() - start.getTime()) / 86_400_000
  return ahead >= 0 && ahead <= MAX_AHEAD_DAYS
}

function isWebLink(value: string): boolean {
  try {
    const parsed = new URL(value)
    return (parsed.protocol === 'https:' || parsed.protocol === 'http:') && parsed.hostname !== ''
  } catch {
    return false
  }
}

/** Checks what the visitor typed. The API repeats these checks; this only helps the form. */
export function validateSubmission(draft: SubmissionDraft, today: Date): SubmissionResult {
  const errors: SubmissionResult['errors'] = {}
  const title = draft.title.trim()
  if (title.length < 3 || title.length > 160) errors.title = 'title'
  if (!isPlausibleDay(draft.startDate, today)) errors.startDate = 'startDate'
  if (!(ISLANDS as readonly string[]).includes(draft.island)) errors.island = 'island'
  const url = draft.url.trim()
  if (url !== '' && !isWebLink(url)) errors.url = 'url'

  if (Object.keys(errors).length > 0) return { errors, payload: null }
  return {
    errors,
    payload: {
      title,
      description: draft.description.trim(),
      start_date: draft.startDate,
      island: draft.island as Island,
      municipality: draft.municipality.trim() || undefined,
      venue: draft.venue.trim() || undefined,
      url: url || undefined,
      honeypot: draft.honeypot,
    },
  }
}
