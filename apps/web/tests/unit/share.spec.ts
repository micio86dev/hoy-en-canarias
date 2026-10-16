import { describe, expect, it } from 'vitest'
import { shareTargets } from '../../app/utils/share'

const page = 'https://hoyencanarias.es/evento/jazz-bc4511'

describe('share links', () => {
  const targets = shareTargets({ url: page, title: 'Concierto de jazz & más', text: 'Este fin de semana' })
  const byId = Object.fromEntries(targets.map((t) => [t.id, t.href]))

  it('offers the main channels, each with a secure HTTPS destination', () => {
    expect(Object.keys(byId).sort()).toEqual(['email', 'facebook', 'telegram', 'whatsapp', 'x'])
    for (const href of Object.values(byId).filter((h) => h.startsWith('http'))) {
      expect(href.startsWith('https://')).toBe(true)
    }
  })

  it('encodes the page address and the title so special characters survive', () => {
    expect(byId.whatsapp).toContain(`text=${encodeURIComponent(`Concierto de jazz & más Este fin de semana ${page}`)}`)
    expect(byId.facebook).toContain(`u=${encodeURIComponent(page)}`)
    expect(byId.telegram).toContain(`url=${encodeURIComponent(page)}`)
  })

  it('never adds tracking parameters of its own', () => {
    for (const href of Object.values(byId)) {
      expect(href).not.toMatch(/utm_|fbclid|ref=/)
    }
  })

  it('builds an email with a subject and body', () => {
    expect(byId.email.startsWith('mailto:?subject=')).toBe(true)
    expect(byId.email).toContain(encodeURIComponent(page))
  })
})
