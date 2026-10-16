<template>
  <div class="flex flex-col gap-12">
    <section aria-labelledby="home-title" class="grid gap-6 lg:grid-cols-[1.2fr_1fr] lg:items-end">
      <div class="space-y-4">
        <h1 id="home-title" class="font-display text-4xl font-semibold leading-tight text-ocean sm:text-5xl">
          {{ t('home.heroTitle') }}
        </h1>
        <p class="max-w-prose text-lg text-ink-muted">{{ t('home.heroLead') }}</p>
      </div>
      <SearchForm @search="onSearch" />
    </section>

    <section aria-labelledby="today-title" class="space-y-4">
      <h2 id="today-title" class="font-display text-2xl font-semibold">{{ t('home.todayTitle') }}</h2>
      <p v-if="today.pending.value" class="text-ink-muted">{{ t('common.loading') }}</p>
      <p v-else-if="!today.data.value?.items.length" class="text-ink-muted">{{ t('home.empty') }}</p>
      <ul v-else class="grid gap-6 sm:grid-cols-2 lg:grid-cols-3" role="list">
        <li v-for="event in today.data.value.items" :key="event.id">
          <EventCard :event="event" />
        </li>
      </ul>
    </section>

    <section aria-labelledby="islands-title" class="space-y-4">
      <h2 id="islands-title" class="font-display text-2xl font-semibold">{{ t('home.islandsTitle') }}</h2>
      <ul class="flex flex-wrap gap-2" role="list">
        <li v-for="island in visibleCounts(islands.data.value?.items ?? [])" :key="island.key">
          <NuxtLinkLocale
            :to="{ path: '/buscar', query: { island: island.key } }"
            class="inline-flex items-center gap-2 rounded-pill bg-sand px-4 py-2 text-sm font-medium hover:bg-ocean-soft"
          >
            {{ island.key }}
            <span class="text-ink-muted">{{ island.count }}</span>
          </NuxtLinkLocale>
        </li>
      </ul>
    </section>

    <section aria-labelledby="categories-title" class="space-y-4">
      <h2 id="categories-title" class="font-display text-2xl font-semibold">{{ t('home.categoriesTitle') }}</h2>
      <ul class="flex flex-wrap gap-2" role="list">
        <li v-for="category in visibleCounts(categories.data.value?.items ?? [])" :key="category.key">
          <NuxtLinkLocale
            :to="{ path: '/buscar', query: { category: category.key } }"
            class="inline-flex items-center gap-2 rounded-pill border border-ink/15 px-4 py-2 text-sm hover:border-ocean"
          >
            {{ t(`categories.${category.key}`) }}
            <span class="text-ink-muted">{{ category.count }}</span>
          </NuxtLinkLocale>
        </li>
      </ul>
    </section>

    <section aria-labelledby="upcoming-title" class="space-y-4">
      <div class="flex items-baseline justify-between gap-4">
        <h2 id="upcoming-title" class="font-display text-2xl font-semibold">{{ t('home.upcomingTitle') }}</h2>
        <NuxtLinkLocale to="/buscar" class="text-sm font-medium text-link">{{ t('home.seeAll') }}</NuxtLinkLocale>
      </div>
      <ul class="grid gap-6 sm:grid-cols-2 lg:grid-cols-3" role="list">
        <li v-for="event in upcomingRows" :key="event.id">
          <EventCard :event="event" />
        </li>
      </ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import type { CountList, EventPage } from '~/types/api'
import type { SearchFilters } from '~/utils/search-params'
import { filtersToRouteQuery } from '~/utils/search-params'
import { completeRows, upcomingWithoutToday, visibleCounts } from '~/utils/home-sections'

const { t } = useI18n()
const localePath = useLocalePath()
const router = useRouter()

const today = await useFetch<EventPage>('/api/v1/events', { query: { preset: 'today', limit: 6 }, key: 'home-today' })
const upcoming = await useFetch<EventPage>('/api/v1/events', { query: { limit: 6 }, key: 'home-upcoming' })
const islands = await useFetch<CountList>('/api/v1/islands', { key: 'home-islands' })
const categories = await useFetch<CountList>('/api/v1/categories', { key: 'home-categories' })

const upcomingRows = computed(() =>
  completeRows(upcomingWithoutToday(upcoming.data.value?.items ?? [], today.data.value?.items ?? []), 3),
)

function onSearch(query: { q?: string; preset?: SearchFilters['preset']; island?: SearchFilters['island']; free?: boolean }): void {
  router.push({ path: localePath('/buscar'), query: filtersToRouteQuery({ ...query }) })
}

useSiteSeo(() => ({ title: t('site.name'), description: t('site.description'), path: '/' }))
</script>
