export type Rgb = [number, number, number]

const HEX = /^#([0-9a-fA-F]{6})$/

export function parseHex(hex: string): Rgb {
  const match = HEX.exec(hex)
  if (!match?.[1]) {
    throw new Error(`expected a six-digit hex colour, got "${hex}"`)
  }
  const value = Number.parseInt(match[1], 16)
  return [(value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff]
}

function channel(value: number): number {
  const srgb = value / 255
  return srgb <= 0.03928 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4
}

export function relativeLuminance(hex: string): number {
  const [r, g, b] = parseHex(hex).map(channel) as Rgb
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** WCAG 2.x contrast ratio between two hex colours, from 1 to 21. */
export function contrastRatio(a: string, b: string): number {
  const [light, dark] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x) as [
    number,
    number,
  ]
  return (light + 0.05) / (dark + 0.05)
}
