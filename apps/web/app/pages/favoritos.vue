<template>
  <div class="mx-auto max-w-6xl space-y-6 px-4 py-8">
    <h1 class="font-display text-3xl font-semibold text-ocean">{{ t('favorites.title') }}</h1>
    <p class="text-ink-muted">{{ t('favorites.intro') }}</p>

    <p v-if="!loaded" class="text-ink-muted">{{ t('favorites.loading') }}</p>
    <p v-else-if="events.length === 0" class="text-ink-muted">{{ t('favorites.empty') }}</p>

    <ul v-else class="grid gap-6 sm:grid-cols-2 lg:grid-cols-3" role="list">
      <li v-for="event in events" :key="event.slug" class="space-y-2">
        <EventCard :event="event" />
        <FavoriteButton :slug="event.slug" />
      </li>
    </ul>
    <p v-if="unavailable > 0" class="text-sm text-ink-muted">{{ t('favorites.unavailable', { count: unavailable }) }}</p>
  </div>
</template>

<script setup lang="ts">
import type { EventSummary } from '~/types/api'

const { t } = useI18n()
const { slugs, load } = useFavorites()

// A private list: never indexed, and it adds nothing to search results.
useSiteSeo(() => ({
  title: t('favorites.title'),
  description: t('favorites.intro'),
  path: '/favoritos',
  noindex: true,
}))

const loaded = ref(false)
const events = ref<EventSummary[]>([])
const unavailable = ref(0)

onMounted(async () => {
  load()
  const wanted = [...slugs.value]
  const results = await Promise.allSettled(
    wanted.map((slug) => $fetch<EventSummary>(`/api/v1/events/${slug}`)),
  )
  events.value = results.flatMap((result) => (result.status === 'fulfilled' ? [result.value] : []))
  // Events that disappeared from the catalogue are counted, never invented.
  unavailable.value = results.length - events.value.length
  loaded.value = true
})
</script>
