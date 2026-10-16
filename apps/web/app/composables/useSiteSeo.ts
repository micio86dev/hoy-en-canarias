import { canonicalPath, type SiteLocale } from '~/utils/seo'

interface SiteSeoOptions {
  title: string
  description: string
  /** Spanish (unprefixed) path of the page, for example "/islas/tenerife". */
  path: string
  noindex?: boolean
  image?: string | null
}

/** Title, description, canonical and Open Graph for one page, in the active locale. */
export function useSiteSeo(options: () => SiteSeoOptions): void {
  const { locale } = useI18n()
  const config = useRuntimeConfig()
  const siteUrl = (config.public.siteUrl as string).replace(/\/$/, '')

  const canonical = computed(() => `${siteUrl}${canonicalPath(locale.value as SiteLocale, options().path)}`)

  useSeoMeta({
    title: () => options().title,
    description: () => options().description,
    ogTitle: () => options().title,
    ogDescription: () => options().description,
    ogUrl: () => canonical.value,
    ogSiteName: 'Hoy en Canarias',
    ogImage: () => options().image ?? undefined,
    twitterCard: () => (options().image ? 'summary_large_image' : 'summary'),
    robots: () => (options().noindex ? 'noindex, follow' : 'index, follow'),
  })

  useHead(() => ({
    link: [{ rel: 'canonical', href: canonical.value }],
  }))
}
