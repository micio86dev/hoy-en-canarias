import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

const PAGES = [
  { name: 'homepage', path: '/' },
  { name: 'search', path: '/buscar' },
  { name: 'islands', path: '/islas' },
  { name: 'map', path: '/mapa' },
]

for (const { name, path } of PAGES) {
  test(`${name} has no WCAG 2.2 AA violations`, async ({ page }) => {
    await page.goto(path)
    await expect(page.getByRole('main')).toBeVisible()

    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag22aa']).analyze()

    expect(results.violations.map((violation) => `${violation.id}: ${violation.help}`)).toEqual([])
  })
}
