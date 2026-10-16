import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

// Screenshot capture for the design review. Runs only when VISUAL=1, against the E2E servers.
const OUT = fileURLToPath(new URL('../../../../../docs/screenshots', import.meta.url))
const SHOTS = [
  { name: 'home', path: '/' },
  { name: 'search', path: '/buscar' },
  { name: 'event', path: '' },
  { name: 'map', path: '/mapa' },
]

test.skip(process.env.VISUAL !== '1', 'set VISUAL=1 to capture screenshots')

for (const viewport of [
  { label: 'desktop', width: 1440, height: 900 },
  { label: 'mobile', width: 390, height: 844 },
]) {
  test(`capture ${viewport.label} screenshots`, async ({ page }) => {
    mkdirSync(OUT, { recursive: true })
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    for (const shot of SHOTS) {
      if (shot.name === 'event') {
        // Slugs carry the importer id, which differs on every fresh database: follow a real link.
        await page.goto('/buscar')
        const href = await page.getByRole('link', { name: 'Concierto de jazz en el puerto' }).first().getAttribute('href')
        await page.goto(href ?? '/buscar')
      } else {
        await page.goto(shot.path)
      }
      await expect(page.getByRole('main')).toBeVisible()
      if (shot.name === 'map') {
        await expect(page.locator('.leaflet-container')).toBeVisible({ timeout: 15_000 })
      }
      await page.waitForTimeout(400)
      await page.screenshot({ path: `${OUT}/${shot.name}-${viewport.label}.png`, fullPage: true })
    }
  })
}
