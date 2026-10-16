<template>
  <div class="space-y-10">
    <header class="space-y-3">
      <h1 class="font-display text-4xl font-semibold text-ocean">{{ island }}</h1>
      <p class="text-lg text-ink-muted">{{ t(countKey('home.upcomingCount', total), { count: total }) }}</p>
      <ul v-if="municipalities.length" class="flex flex-wrap gap-2 pt-2" role="list">
        <li v-for="item in municipalities" :key="item.key">
          <NuxtLinkLocale :to="{ path: '/buscar', query: { island, municipality: item.key } }" class="inline-flex gap-2 rounded-pill bg-sand px-3 py-1.5 text-sm hover:bg-ocean-soft">
            {{ item.key }} <span class="text-ink-muted">{{ item.count }}</span>
          </NuxtLinkLocale>
        </li>
      </ul>
    </header>
    <ul class="grid gap-6 sm:grid-cols-2 lg:grid-cols-3" role="list">
      <li v-for="event in events" :key="event.id"><EventCard :event="event" /></li>
    </ul>
    <p v-if="!events.length" class="text-ink-muted">{{ t('home.empty') }}</p>
  </div>
</template>

<script setup lang="ts">
import { countKey } from '~/utils/plural'
import type { CountList, EventPage } from '~/types/api'
import { islandFromSegment } from '~/utils/route-segments'

const { t } = useI18n()
const route = useRoute()
const island = islandFromSegment(String(route.params.island))
if (!island) {
  throw createError({ statusCode: 404, statusMessage: 'Island not found', fatal: true })
}

const { data: page } = await useAsyncData(`island-${island}`, () =>
  $fetch<EventPage>('/api/v1/events', { query: { island, limit: 24 } }),
)
const { data: places } = await useAsyncData(`island-places-${island}`, () =>
  $fetch<CountList>('/api/v1/municipalities', { query: { island } }),
)

const events = computed(() => page.value?.items ?? [])
const total = computed(() => page.value?.total ?? 0)
const municipalities = computed(() => places.value?.items ?? [])

useSeoMeta({ title: () => `${island} · ${t('site.name')}`, description: () => t('site.description') })
</script>
