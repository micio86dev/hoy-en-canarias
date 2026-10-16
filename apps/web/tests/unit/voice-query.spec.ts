import { describe, expect, it } from 'vitest'
import { parseVoiceQuery } from '../../app/utils/voice-query'

describe('voice query parser', () => {
  it('extracts a Spanish date shortcut, island and remaining text', () => {
    expect(parseVoiceQuery('conciertos de jazz mañana en Tenerife', { locale: 'es' })).toEqual({
      q: 'conciertos de jazz',
      preset: 'tomorrow',
      island: 'Tenerife',
    })
  })

  it('extracts English free-only and weekend intent', () => {
    expect(parseVoiceQuery('free events this weekend in Lanzarote', { locale: 'en' })).toEqual({
      q: 'events',
      preset: 'weekend',
      island: 'Lanzarote',
      free: true,
    })
  })

  it('extracts Italian today and free intent for a multi-word island', () => {
    expect(parseVoiceQuery('eventi gratis oggi a Gran Canaria', { locale: 'it' })).toEqual({
      q: 'eventi',
      preset: 'today',
      island: 'Gran Canaria',
      free: true,
    })
  })

  it('matches accent-insensitively, so "manana" still means tomorrow', () => {
    expect(parseVoiceQuery('manana', { locale: 'es' })).toEqual({ preset: 'tomorrow' })
  })

  it('returns an empty object for empty input', () => {
    expect(parseVoiceQuery('   ', { locale: 'es' })).toEqual({})
  })

  it('never invents an island that was not spoken', () => {
    expect(parseVoiceQuery('mercado de artesanía', { locale: 'es' })).toEqual({
      q: 'mercado de artesanía',
    })
  })
})
