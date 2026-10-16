export default defineEventHandler((event) => {
  const siteUrl = (useRuntimeConfig(event).public.siteUrl as string).replace(/\/$/, '')
  setResponseHeader(event, 'content-type', 'text/markdown; charset=utf-8')
  return [
    '# Hoy en Canarias',
    '',
    '> Public, free-to-browse listing of events across the eight Canary Islands.',
    '',
    'Each event page gives the date and time in the Canary Islands calendar, the venue, the',
    'source it was collected from and the official link. Missing information is left blank,',
    'never guessed.',
    '',
    '## Sections',
    `- [Events this week](${siteUrl}/buscar?preset=weekend)`,
    `- [Islands](${siteUrl}/islas)`,
    `- [Categories](${siteUrl}/categorias)`,
    `- [Map](${siteUrl}/mapa)`,
    `- [Sitemap](${siteUrl}/sitemap.xml)`,
    '',
  ].join('\n')
})
