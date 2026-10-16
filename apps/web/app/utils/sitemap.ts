import { SITE_LOCALES, canonicalPath, type SiteLocale } from './seo'

export interface SitemapPath {
  /** Spanish (unprefixed) path, for example "/evento/slug". */
  path: string
  /** ISO 8601 date of the last real change; omitted when unknown. */
  lastmod?: string
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/\'/g, '&apos;')
}

/** One <url> per locale, each listing every language alternate, as search engines expect. */
export function buildSitemapXml(siteUrl: string, entries: SitemapPath[]): string {
  const origin = siteUrl.replace(/\/$/, '')
  const urls = entries.flatMap((entry) => {
    const alternates = [
      ...SITE_LOCALES.map(
        (locale: SiteLocale) =>
          `    <xhtml:link rel="alternate" hreflang="${locale}" href="${escapeXml(origin + canonicalPath(locale, entry.path))}"/>`,
      ),
      `    <xhtml:link rel="alternate" hreflang="x-default" href="${escapeXml(origin + canonicalPath('es', entry.path))}"/>`,
    ].join('\n')
    const lastmod = entry.lastmod ? `\n    <lastmod>${escapeXml(entry.lastmod)}</lastmod>` : ''
    return SITE_LOCALES.map(
      (locale: SiteLocale) =>
        `  <url>\n    <loc>${escapeXml(origin + canonicalPath(locale, entry.path))}</loc>${lastmod}\n${alternates}\n  </url>`,
    )
  })
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ...urls,
    '</urlset>',
  ].join('\n')
}
