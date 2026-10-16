<template>
  <div class="space-y-10">
    <header class="space-y-3">
      <h1 class="font-display text-4xl font-semibold text-ocean">{{ t(`categories.${category}`) }}</h1>
      <p class="text-lg text-ink-muted">{{ t(countKey('home.upcomingCount', total), { count: total }) }}</p>
    </header>
    <ul class="grid gap-6 sm:grid-cols-2 lg:grid-cols-3" role="list">
      <li v-for="event in events" :key="event.id"><EventCard :event="event" /></li>
    </ul>
    <p v-if="!events.length" class="text-ink-muted">{{ t('home.empty') }}</p>
  </div>
</template>

<script setup lang="ts">
import { countKey } from '~/utils/plural'
import { CATEGORIES, type Category } from '~/utils/constants'
import type { EventPage } from '~/types/api'

const { t } = useI18n()
const route = useRoute()
const requested = String(route.params.category)
if (!(CATEGORIES as readonly string[]).includes(requested)) {
  throw createError({ statusCode: 404, statusMessage: 'Category not found', fatal: true })
}
const category = requested as Category

const { data: page } = await useAsyncData(`category-${category}`, () =>
  $fetch<EventPage>('/api/v1/events', { query: { category, limit: 24 } }),
)

const events = computed(() => page.value?.items ?? [])
const total = computed(() => page.value?.total ?? 0)

useSiteSeo(() => ({ title: `${t(`categories.${category}`)} · ${t('site.name')}`, description: t('site.description'), path: `/categorias/${category}` }))
</script>
