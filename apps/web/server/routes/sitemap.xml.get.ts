import { buildSitemapXml } from '../../app/utils/sitemap'

const STATIC_PATHS = ['/', '/buscar', '/islas', '/categorias', '/mapa']

interface SitemapData {
  items: Array<{ slug: string; lastmod: string }>
}

export default defineEventHandler(async (event) => {
  const config = useRuntimeConfig(event)
  const siteUrl = (config.public.siteUrl as string).replace(/\/$/, '')
  let data: SitemapData
  try {
    data = await $fetch<SitemapData>('/api/v1/sitemap-data', { baseURL: config.apiBase as string })
  } catch {
    throw createError({ statusCode: 502, statusMessage: 'Sitemap source unavailable' })
  }

  const entries = [
    ...STATIC_PATHS.map((path) => ({ path })),
    ...data.items.map((item) => ({ path: `/evento/${item.slug}`, lastmod: item.lastmod })),
  ]
  setResponseHeader(event, 'content-type', 'application/xml; charset=utf-8')
  setResponseHeader(event, 'cache-control', 'public, max-age=3600, stale-while-revalidate=86400')
  return buildSitemapXml(siteUrl, entries)
})
