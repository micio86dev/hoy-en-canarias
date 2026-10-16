import { ISLANDS, type Island, type Preset } from './constants'

export type VoiceLocale = 'es' | 'en' | 'it'

export interface VoiceQuery {
  q?: string
  preset?: Preset
  island?: Island
  free?: boolean
}

export interface ParseOptions {
  locale: VoiceLocale
  today: Date
}

// Phrases are matched on normalised tokens (lower case, no diacritics), longest first.
const DATE_PHRASES: Record<VoiceLocale, Array<[Preset, string[]]>> = {
  es: [
    ['weekend', ['fin de semana']],
    ['tomorrow', ['manana']],
    ['today', ['hoy']],
  ],
  en: [
    ['weekend', ['this weekend', 'weekend']],
    ['tomorrow', ['tomorrow']],
    ['today', ['today', 'tonight']],
  ],
  it: [
    ['weekend', ['fine settimana', 'weekend']],
    ['tomorrow', ['domani']],
    ['today', ['oggi', 'stasera']],
  ],
}

const FREE_PHRASES: Record<VoiceLocale, string[]> = {
  es: ['gratis', 'gratuito', 'gratuita'],
  en: ['free'],
  it: ['gratis', 'gratuito', 'gratuita'],
}

// Words that only connect a removed phrase to the rest ("in Tenerife", "a Gran Canaria").
const CONNECTORS = new Set(['en', 'in', 'a', 'di', 'da', 'at', 'on'])

function normalise(value: string): string {
  return value.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()
}

const ISLAND_PHRASES: Array<[Island, string[]]> = ISLANDS.map((island) => [island, [normalise(island)]])

interface Match {
  kind: 'preset' | 'island' | 'free'
  value: Preset | Island | true
}

function allPhrases(locale: VoiceLocale): Array<[string, Match]> {
  const phrases: Array<[string, Match]> = []
  for (const [preset, list] of DATE_PHRASES[locale]) {
    for (const phrase of list) phrases.push([phrase, { kind: 'preset', value: preset }])
  }
  for (const phrase of FREE_PHRASES[locale]) phrases.push([phrase, { kind: 'free', value: true }])
  for (const [island, list] of ISLAND_PHRASES) {
    for (const phrase of list) phrases.push([phrase, { kind: 'island', value: island }])
  }
  return phrases.sort((a, b) => b[0].split(' ').length - a[0].split(' ').length)
}

/**
 * Turns a spoken or typed sentence into structured filters. Deterministic and offline:
 * unrecognised words stay in the free-text query, and an island is set only when named.
 */
export function parseVoiceQuery(text: string, options: ParseOptions): VoiceQuery {
  const tokens = text.trim().split(/\s+/).filter(Boolean)
  const phrases = allPhrases(options.locale)
  const result: VoiceQuery = {}
  const kept: string[] = []

  let index = 0
  while (index < tokens.length) {
    const match = phrases.find(([phrase]) => {
      const width = phrase.split(' ').length
      const candidate = tokens.slice(index, index + width).map(normalise).join(' ')
      return candidate === phrase
    })
    if (match) {
      const [phrase, hit] = match
      if (hit.kind === 'preset') result.preset = hit.value as Preset
      if (hit.kind === 'island') result.island = hit.value as Island
      if (hit.kind === 'free') result.free = true
      index += phrase.split(' ').length
      continue
    }
    kept.push(tokens[index] as string)
    index += 1
  }

  while (kept.length > 0 && CONNECTORS.has(normalise(kept[kept.length - 1] as string))) {
    kept.pop()
  }
  const q = kept.join(' ').trim()
  if (q) result.q = q
  return result
}
