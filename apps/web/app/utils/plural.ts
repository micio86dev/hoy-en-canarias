/** Picks the singular or plural message key. Explicit keys avoid ICU syntax the i18n module cannot parse. */
export function countKey(base: string, count: number): string {
  return `${base}${count === 1 ? 'One' : 'Other'}`
}
