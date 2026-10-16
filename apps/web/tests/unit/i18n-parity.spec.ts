import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const LOCALES = ['es', 'en', 'it'] as const

type Catalogue = Record<string, unknown>

function load(locale: string): Catalogue {
  const url = new URL(`../../i18n/locales/${locale}.json`, import.meta.url)
  return JSON.parse(readFileSync(url, 'utf8')) as Catalogue
}

function flatten(node: unknown, prefix = ''): Record<string, string> {
  if (typeof node === 'string') {
    return { [prefix]: node }
  }
  if (node !== null && typeof node === 'object') {
    return Object.entries(node).reduce<Record<string, string>>(
      (acc, [key, value]) => ({ ...acc, ...flatten(value, prefix ? `${prefix}.${key}` : key) }),
      {},
    )
  }
  throw new Error(`unsupported catalogue value at "${prefix}"`)
}

describe('locale catalogues', () => {
  const flat = Object.fromEntries(LOCALES.map((locale) => [locale, flatten(load(locale))]))

  it('expose exactly the same keys in Spanish, English and Italian', () => {
    const reference = Object.keys(flat.es).sort()
    for (const locale of ['en', 'it'] as const) {
      expect(Object.keys(flat[locale]).sort(), `keys of ${locale}`).toEqual(reference)
    }
  })

  it('never ship an empty translation', () => {
    for (const locale of LOCALES) {
      const empty = Object.entries(flat[locale])
        .filter(([, value]) => value.trim() === '')
        .map(([key]) => key)
      expect(empty, `empty values in ${locale}`).toEqual([])
    }
  })
})
