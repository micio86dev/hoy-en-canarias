<template>
  <div class="grid gap-8 lg:grid-cols-[18rem_1fr]">
    <aside class="space-y-6 lg:sticky lg:top-6 lg:self-start">
      <h1 class="font-display text-3xl font-semibold text-ocean">{{ t('search.title') }}</h1>
      <SearchForm :initial-text="initialText" @search="onSearch" />
      <FilterPanel :filters="filters" @update="onFilters" @clear="clearFilters" />
    </aside>

    <section aria-live="polite" aria-labelledby="results-title" class="space-y-6">
      <h2 id="results-title" class="sr-only">{{ t('search.results', { count: total }) }}</h2>
      <p v-if="results.pending.value" class="text-ink-muted">{{ t('common.loading') }}</p>
      <p v-else-if="results.error.value" class="text-ink-muted" role="alert">{{ t('common.error') }}</p>
      <p v-else class="text-sm font-medium text-ink-muted">{{ t('search.results', { count: total }) }}</p>

      <p v-if="!results.pending.value && !results.error.value && total === 0" class="rounded-card bg-sand p-6">
        {{ t('search.noResults') }}
      </p>

      <ul v-if="items.length" class="grid gap-6 sm:grid-cols-2" role="list">
        <li v-for="event in items" :key="event.id">
          <EventCard :event="event" />
        </li>
      </ul>

      <nav v-if="totalPages > 1" :aria-label="t('search.pageOf', { page: currentPage })" class="flex items-center justify-between gap-4">
        <NuxtLinkLocale
          v-if="currentPage > 1"
          :to="{ path: '/buscar', query: pageQuery(currentPage - 1) }"
          class="text-sm font-medium text-link"
        >
          {{ t('search.previous') }}
        </NuxtLinkLocale>
        <span class="text-sm text-ink-muted">{{ t('search.pageOf', { page: currentPage }) }}</span>
        <NuxtLinkLocale
          v-if="currentPage < totalPages"
          :to="{ path: '/buscar', query: pageQuery(currentPage + 1) }"
          class="text-sm font-medium text-link"
        >
          {{ t('search.next') }}
        </NuxtLinkLocale>
      </nav>
    </section>
  </div>
</template>

<script setup lang="ts">
import type { EventPage } from '~/types/api'
import { filtersFromQuery, filtersToApiQuery, filtersToRouteQuery, type SearchFilters } from '~/utils/search-params'
import type { VoiceQuery } from '~/utils/voice-query'

const { t } = useI18n()
const route = useRoute()
const localePath = useLocalePath()
const router = useRouter()

const filters = computed<SearchFilters>(() => filtersFromQuery(route.query))
const initialText = computed(() => filters.value.q ?? '')
const currentPage = computed(() => filters.value.page ?? 1)

const results = await useFetch<EventPage>('/api/v1/events', {
  query: computed(() => Object.fromEntries(filtersToApiQuery(filters.value))),
  key: () => `search-${route.fullPath}`,
  watch: [() => route.fullPath],
})

const items = computed(() => results.data.value?.items ?? [])
const total = computed(() => results.data.value?.total ?? 0)
const limit = computed(() => results.data.value?.limit ?? 20)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / limit.value)))

function navigate(next: SearchFilters): void {
  router.push({ path: localePath('/buscar'), query: filtersToRouteQuery(next) })
}

function onSearch(parsed: VoiceQuery): void {
  navigate({ ...filters.value, page: undefined, ...parsed })
}

function onFilters(next: SearchFilters): void {
  navigate({ ...next, q: filters.value.q })
}

function clearFilters(): void {
  navigate({})
}

function pageQuery(page: number): Record<string, string> {
  return filtersToRouteQuery({ ...filters.value, page })
}

const hasFilters = computed(() => Object.keys(filters.value).length > 0)
useSiteSeo(() => ({
  title: t('search.title'),
  description: t('site.description'),
  path: '/buscar',
  noindex: hasFilters.value,
}))
</script>
