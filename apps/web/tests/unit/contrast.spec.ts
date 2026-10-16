import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { contrastRatio, parseHex } from '../../app/utils/color'

const tokens = readFileSync(new URL('../../app/assets/css/tokens.css', import.meta.url), 'utf8')

function token(name: string): string {
  const match = tokens.match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})`))
  if (!match?.[1]) {
    throw new Error(`token --color-${name} is missing`)
  }
  return match[1]
}

describe('colour tokens', () => {
  it('parses six-digit hex colours', () => {
    expect(parseHex('#0b3954')).toEqual([11, 57, 84])
  })

  it('computes the WCAG contrast ratio', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 1)
  })

  const pairs: Array<[string, string]> = [
    ['ink', 'paper'],
    ['ink-muted', 'paper'],
    ['ink', 'sand'],
    ['ink-muted', 'sand'],
    ['paper', 'ocean'],
    ['accent-ink', 'accent'],
    ['link', 'paper'],
  ]

  for (const [foreground, background] of pairs) {
    it(`${foreground} on ${background} meets WCAG AA normal text (4.5:1)`, () => {
      const ratio = contrastRatio(token(foreground), token(background))
      expect(ratio).toBeGreaterThanOrEqual(4.5)
    })
  }
})
