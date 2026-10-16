import { expect, test } from '@playwright/test'

const JAZZ = 'Concierto de jazz en el puerto'
const CANA = 'Fiesta de la Caña Dulce'

test.describe('visitor journeys', () => {
  test('homepage loads with the hero and upcoming events', async ({ page }) => {
    await page.goto('/')

    await expect(page.getByRole('heading', { level: 1, name: 'Qué pasa hoy en Canarias' })).toBeVisible()
    await expect(page.getByRole('link', { name: JAZZ }).first()).toBeVisible()
  })

  test('a visitor searches for an event and finds it', async ({ page }) => {
    await page.goto('/')

    await page.getByRole('searchbox').fill('jazz')
    await page.getByRole('button', { name: 'Buscar' }).first().click()

    await expect(page).toHaveURL(/\/buscar\?q=jazz/)
    await expect(page.getByRole('link', { name: JAZZ })).toBeVisible()
  })

  test('filters are applied to the URL and can be cleared', async ({ page }) => {
    await page.goto('/buscar')
    await expect(page.getByRole('link', { name: CANA })).toBeVisible()

    await page.getByLabel('Isla').selectOption('Tenerife')

    await expect(page).toHaveURL(/island=Tenerife/)
    await expect(page.getByRole('link', { name: CANA })).toHaveCount(0)
    await expect(page.getByRole('link', { name: JAZZ })).toBeVisible()

    await page.getByRole('button', { name: 'Limpiar filtros' }).click()
    await expect(page).not.toHaveURL(/island=/)
    await expect(page.getByRole('link', { name: CANA })).toBeVisible()
  })

  test('island navigation opens the island page', async ({ page }) => {
    await page.goto('/islas')

    await page.getByRole('link', { name: /^Tenerife/ }).click()

    await expect(page).toHaveURL(/\/islas\/tenerife$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Tenerife' })).toBeVisible()
  })

  test('an event detail page opens from the results', async ({ page }) => {
    await page.goto('/buscar')

    await page.getByRole('link', { name: JAZZ }).click()

    await expect(page.getByRole('heading', { level: 1, name: JAZZ })).toBeVisible()
    await expect(page.getByText('Jazz al atardecer.')).toBeVisible()
  })

  test('localised navigation keeps the page and sets the language', async ({ page }) => {
    await page.goto('/en/')
    await expect(page.locator('html')).toHaveAttribute('lang', 'en-GB')

    await page.getByRole('navigation', { name: 'Language' }).getByRole('link', { name: 'Italiano' }).click()

    await expect(page).toHaveURL(/\/it\/?$/)
    await expect(page.locator('html')).toHaveAttribute('lang', 'it-IT')
  })

  test('no geolocation request is made on load', async ({ page }) => {
    await page.addInitScript(() => {
      ;(window as unknown as { __geoCalls: number }).__geoCalls = 0
      navigator.geolocation.getCurrentPosition = () => {
        ;(window as unknown as { __geoCalls: number }).__geoCalls += 1
      }
    })

    await page.goto('/mapa')
    await expect(page.getByRole('link', { name: JAZZ }).first()).toBeVisible()

    const calls = await page.evaluate(() => (window as unknown as { __geoCalls: number }).__geoCalls)
    expect(calls).toBe(0)
  })

  test('the map and its list alternative both work', async ({ page }) => {
    await page.goto('/mapa')

    await expect(page.getByRole('region', { name: 'Mapa de eventos' })).toBeVisible()
    await expect(page.locator('.leaflet-container')).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('link', { name: JAZZ }).first()).toBeVisible()
  })

  test('an empty search shows an honest empty state', async ({ page }) => {
    await page.goto('/buscar?q=zzqxwv')

    await expect(page.getByText('No hay eventos con estos filtros.')).toBeVisible()
  })

  test('an unknown event shows the not-found page', async ({ page }) => {
    const response = await page.goto('/evento/no-existe-000000')

    expect(response?.status()).toBe(404)
    await expect(page.getByRole('heading', { level: 1, name: 'Página no encontrada' })).toBeVisible()
  })

  test('an event page carries canonical, robots and Event structured data', async ({ page }) => {
    await page.goto('/buscar')
    await page.getByRole('link', { name: JAZZ }).click()
    await expect(page.getByRole('heading', { level: 1, name: JAZZ })).toBeVisible()

    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', /\/evento\/concierto-de-jazz/)
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'index, follow')

    const jsonLd = await page.locator('script[type="application/ld+json"]').first().textContent()
    const data = JSON.parse(jsonLd ?? '{}') as { '@type'?: string; name?: string; startDate?: string }
    expect(data['@type']).toBe('Event')
    expect(data.name).toBe(JAZZ)
    expect(data.startDate).toMatch(/T\d{2}:\d{2}:\d{2}\+0[01]:00$/)
  })

  test('filtered search results are not indexed', async ({ page }) => {
    await page.goto('/buscar?island=Tenerife')

    await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', /noindex/)
  })

  test('mobile layout has no horizontal scroll and search stays usable', async ({ page }) => {
    await page.goto('/')

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(1)
    await expect(page.getByRole('searchbox')).toBeVisible()
  })

  test('a broken event image falls back to the placeholder with the brand mark', async ({ page }) => {
    await page.goto('/buscar')
    const card = page.locator('article', { has: page.getByRole('link', { name: JAZZ }) }).first()

    await expect(card.getByTestId('event-placeholder')).toBeVisible({ timeout: 15_000 })
    await expect(card.getByTestId('event-placeholder').getByTestId('brand-mark')).toBeVisible()
    await expect(card.locator('img')).toHaveCount(0)
  })

  test('an event page offers share links to the canonical address', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    await page.goto('/buscar')
    await page.getByRole('link', { name: JAZZ }).click()
    await expect(page.getByRole('heading', { level: 1, name: JAZZ })).toBeVisible()

    const whatsapp = page.getByRole('link', { name: /WhatsApp/ })
    await expect(whatsapp).toHaveAttribute('href', /^https:\/\/wa\.me\/\?text=/)
    await expect(page.getByRole('link', { name: /Facebook/ })).toHaveAttribute('href', /sharer\.php\?u=/)
    await expect(page.getByRole('link', { name: /Correo/ })).toHaveAttribute('href', /^mailto:/)

    await page.getByRole('button', { name: 'Copiar enlace' }).click()
    await expect(page.getByRole('status')).toHaveText('Enlace copiado.')
    const copied = await page.evaluate(() => navigator.clipboard.readText())
    expect(copied).toMatch(/\/evento\/concierto-de-jazz/)
    expect(copied).not.toContain('?')
  })
})
