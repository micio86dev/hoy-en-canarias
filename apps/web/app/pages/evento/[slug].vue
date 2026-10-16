<template>
  <article class="grid gap-10 lg:grid-cols-[1fr_20rem]">
    <div class="space-y-6">
      <div v-if="showHero" class="aspect-[16/9] overflow-hidden rounded-card bg-sand">
        <img
          ref="heroEl"
          :src="current.image_url ?? undefined"
          alt=""
          referrerpolicy="no-referrer"
          class="h-full w-full object-cover"
          @error="heroFailed = true"
        />
      </div>
      <header class="space-y-3">
        <p class="text-sm font-medium text-link">{{ when }}</p>
        <h1 class="font-display text-4xl font-semibold leading-tight text-ocean">{{ event.title }}</h1>
        <p class="text-lg text-ink-muted">{{ place }}</p>
        <div class="flex flex-wrap gap-2 text-xs font-medium">
          <span class="rounded-pill bg-sand px-3 py-1">{{ t(`categories.${event.category}`) }}</span>
          <span v-if="event.is_free === true" class="rounded-pill bg-ocean-soft px-3 py-1 text-ocean">{{ t('event.free') }}</span>
          <span v-if="event.stale" class="rounded-pill bg-accent/30 px-3 py-1">{{ t('event.stale') }}</span>
        </div>
      </header>

      <p v-if="event.description" class="max-w-prose whitespace-pre-line text-lg leading-relaxed">{{ event.description }}</p>

      <dl v-if="event.organizer_name || event.address" class="grid gap-4 text-sm sm:grid-cols-2">
        <div v-if="event.address">
          <dt class="font-medium">{{ t('event.address') }}</dt>
          <dd class="text-ink-muted">{{ event.address }}</dd>
        </div>
        <div v-if="event.organizer_name">
          <dt class="font-medium">{{ t('event.organizer') }}</dt>
          <dd class="text-ink-muted">{{ event.organizer_name }}</dd>
        </div>
      </dl>

      <section v-if="relatedRows.length" aria-labelledby="related-title" class="space-y-4 pt-6">
        <h2 id="related-title" class="font-display text-2xl font-semibold">{{ t('event.related') }}</h2>
        <ul class="grid gap-6 sm:grid-cols-2" role="list">
          <li v-for="item in relatedRows" :key="item.id">
            <EventCard :event="item" />
          </li>
        </ul>
      </section>
    </div>

    <aside class="space-y-6" :aria-label="t('event.practical')">
      <div v-if="event.official_event_url || event.ticket_url" class="space-y-3 rounded-card bg-sand p-5">
        <h2 class="font-semibold">{{ t('event.links') }}</h2>
        <ul class="space-y-2 text-sm" role="list">
          <li v-if="event.official_event_url">
            <a :href="event.official_event_url" target="_blank" rel="noopener noreferrer nofollow" class="font-medium text-link underline underline-offset-4">
              {{ t('event.officialPage') }}<span class="sr-only"> {{ t('event.opensNewTab') }}</span>
            </a>
          </li>
          <li v-if="event.ticket_url">
            <a :href="event.ticket_url" target="_blank" rel="noopener noreferrer nofollow" class="font-medium text-link underline underline-offset-4">
              {{ t('event.tickets') }}<span class="sr-only"> {{ t('event.opensNewTab') }}</span>
            </a>
          </li>
        </ul>
      </div>

      <div class="space-y-2 text-sm">
        <h2 class="font-semibold">{{ t('event.provenance') }}</h2>
        <ul class="space-y-1 text-ink-muted" role="list">
          <li v-for="source in event.sources" :key="source.source_name">{{ source.source_name }}</li>
        </ul>
        <p v-if="verified" class="text-ink-muted">{{ t('event.lastVerified', { date: verified }) }}</p>
      </div>

      <ShareButtons :url="shareUrl" :title="current.title" :text="current.description?.slice(0, 120) ?? undefined" />
    </aside>
  </article>
</template>

<script setup lang="ts">
import type { EventDetail, EventPage } from '~/types/api'
import { formatEventWhen } from '~/utils/dates'
import { buildEventJsonLd, safeJsonLd } from '~/utils/seo'
import { completeRows } from '~/utils/home-sections'

const { t, locale } = useI18n()
const route = useRoute()
const slug = computed(() => String(route.params.slug))

const { data: event } = await useAsyncData(`event-${slug.value}`, async () => {
  try {
    return await $fetch<EventDetail>(`/api/v1/events/${slug.value}`)
  } catch (error) {
    const status = (error as { statusCode?: number }).statusCode
    throw createError({ statusCode: status === 404 ? 404 : 502, statusMessage: 'Event unavailable', fatal: true })
  }
})

const { data: related } = await useAsyncData(`related-${slug.value}`, () =>
  $fetch<EventPage>(`/api/v1/events/${slug.value}/related`),
)

if (!event.value) {
  throw createError({ statusCode: 404, statusMessage: 'Event not found', fatal: true })
}
const current = event.value
const relatedRows = computed(() => completeRows(related.value?.items ?? [], 2))

const when = computed(() =>
  formatEventWhen(
    { startAt: current.start_at, endAt: current.end_at, startDate: current.start_date, endDate: current.end_date, allDay: current.all_day },
    locale.value,
  ),
)

const place = computed(() =>
  [current.venue_name, current.municipality, current.island].filter(Boolean).join(' · ') || t('event.placeUnknown'),
)

const verified = computed(() =>
  current.last_verified_at
    ? new Intl.DateTimeFormat(locale.value, { timeZone: 'Atlantic/Canary', dateStyle: 'medium' }).format(new Date(current.last_verified_at))
    : '',
)

const heroFailed = ref(false)
const heroEl = ref<HTMLImageElement | null>(null)
const showHero = computed(() => Boolean(current.image_url) && !heroFailed.value)
onMounted(() => {
  const image = heroEl.value
  if (image && image.complete && image.naturalWidth === 0) heroFailed.value = true
})

const config = useRuntimeConfig()
const pagePath = `/evento/${current.slug}`
// Share the canonical address, never a URL with query parameters.
const shareUrl = `${String(config.public.siteUrl).replace(/\/$/, '')}${pagePath}`

useSiteSeo(() => ({
  title: `${current.title} · ${t('site.name')}`,
  description: current.description?.slice(0, 160) ?? t('site.description'),
  path: pagePath,
  image: current.image_url,
}))

useHead(() => ({
  script: [
    {
      type: 'application/ld+json',
      innerHTML: safeJsonLd(
        buildEventJsonLd(current, {
          siteUrl: String(config.public.siteUrl).replace(/\/$/, ''),
          url: `${String(config.public.siteUrl).replace(/\/$/, '')}${pagePath}`,
        }),
      ),
    },
  ],
}))
</script>
