export default defineEventHandler((event) => {
  const siteUrl = (useRuntimeConfig(event).public.siteUrl as string).replace(/\/$/, '')
  setResponseHeader(event, 'content-type', 'text/plain; charset=utf-8')
  return [
    'User-agent: *',
    'Allow: /',
    'Disallow: /api/',
    '',
    `Sitemap: ${siteUrl}/sitemap.xml`,
    '',
  ].join('\n')
})
