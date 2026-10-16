import { expect, test } from '@playwright/test'

const JAZZ = 'Concierto de jazz en el puerto'
const CANA = 'Fiesta de la Caña Dulce'

const cardOf = (page: import('@playwright/test').Page, title: string) =>
  page.locator('article', { has: page.getByRole('link', { name: title }) })

test.describe('guest favourites', () => {
  test('a guest saves an event, keeps it after reload, and removes it from the list', async ({ page }) => {
    await page.goto('/buscar')

    const save = cardOf(page, JAZZ).getByRole('button', { name: 'Guardar en favoritos' })
    await expect(save).toHaveAttribute('aria-pressed', 'false')
    await save.click()
    await expect(cardOf(page, JAZZ).getByRole('button', { name: 'Quitar de favoritos' })).toHaveAttribute('aria-pressed', 'true')

    await page.reload()
    await expect(cardOf(page, JAZZ).getByRole('button', { name: 'Quitar de favoritos' })).toHaveAttribute('aria-pressed', 'true')

    await page.goto('/favoritos')
    await expect(page.getByRole('heading', { level: 1, name: 'Mis favoritos' })).toBeVisible()
    await expect(page.getByRole('link', { name: JAZZ })).toBeVisible()
    await expect(page.getByRole('link', { name: CANA })).toHaveCount(0)

    await cardOf(page, JAZZ).getByRole('button', { name: 'Quitar de favoritos' }).click()
    await expect(page.getByText('Aún no tienes favoritos')).toBeVisible()
  })

  test('the favourites page is never indexed', async ({ page }) => {
    await page.goto('/favoritos')
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', /noindex/)
  })
})
