import { describe, expect, it } from 'vitest'
import { validateSubmission, type SubmissionDraft } from '../../app/utils/submission'

const today = new Date('2026-10-16T10:00:00Z')

const draft = (overrides: Partial<SubmissionDraft> = {}): SubmissionDraft => ({
  title: 'Mercadillo de Garachico',
  description: 'Artesanía local cada sábado.',
  startDate: '2026-10-24',
  island: 'Tenerife',
  municipality: 'Garachico',
  venue: '',
  url: 'https://garachico.test/mercadillo',
  honeypot: '',
  ...overrides,
})

describe('submission validation', () => {
  it('accepts a complete, plausible draft and returns the API payload', () => {
    const result = validateSubmission(draft(), today)

    expect(result.errors).toEqual({})
    expect(result.payload).toMatchObject({ title: 'Mercadillo de Garachico', start_date: '2026-10-24', island: 'Tenerife' })
    expect(result.payload).toMatchObject({ honeypot: '' })
  })

  it('reports each problem on its own field', () => {
    const result = validateSubmission(draft({ title: 'ab', startDate: '2026-10-01', island: 'Atlantis' }), today)

    expect(Object.keys(result.errors).sort()).toEqual(['island', 'startDate', 'title'])
    expect(result.payload).toBeNull()
  })

  it('refuses non-web links instead of storing them', () => {
    const result = validateSubmission(draft({ url: 'javascript:alert(1)' }), today)

    expect(result.errors.url).toBeDefined()
  })

  it('accepts an empty link and an empty description', () => {
    const result = validateSubmission(draft({ url: '', description: '' }), today)

    expect(result.errors).toEqual({})
    expect(result.payload).toMatchObject({ url: undefined })
  })

  it('keeps a filled honeypot in the payload so the API can reject it silently', () => {
    const result = validateSubmission(draft({ honeypot: 'spam' }), today)

    expect(result.payload).toMatchObject({ honeypot: 'spam' })
  })
})
