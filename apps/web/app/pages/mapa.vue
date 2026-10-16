<template>
  <div class="space-y-8">
    <header class="max-w-prose space-y-3">
      <h1 class="font-display text-4xl font-semibold text-ocean">{{ t('nav.map') }}</h1>
      <p class="text-lg text-ink-muted">{{ t('map.lead') }}</p>
    </header>

    <div class="flex flex-wrap items-center gap-3">
      <button
        type="button"
        class="rounded-pill border border-ocean px-4 py-2 text-sm font-medium text-ocean hover:bg-ocean-soft"
        @click="locate"
      >
        {{ t('map.locate') }}
      </button>
      <p aria-live="polite" class="text-sm text-ink-muted">{{ locateStatus }}</p>
    </div>

    <ClientOnly>
      <EventMap :points="points" :focus="focus" @bounds-change="onBoundsChange" />
      <template #fallback>
        <div class="flex h-[60vh] min-h-80 w-full items-center justify-center rounded-card bg-sand text-ink-muted" role="status">
          {{ t('common.loading') }}
        </div>
      </template>
    </ClientOnly>

    <section aria-labelledby="map-list-title" class="space-y-4">
      <h2 id="map-list-title" class="font-display text-2xl font-semibold">{{ t('map.listTitle') }}</h2>
      <p v-if="truncated" class="text-sm text-ink-muted">{{ t('map.truncated') }}</p>
      <p v-if="!points.length" class="text-ink-muted">{{ t('map.noPoints') }}</p>
      <ul v-else class="grid gap-3 sm:grid-cols-2" role="list">
        <li v-for="point in points" :key="point.slug" class="rounded-card bg-sand p-4">
          <NuxtLinkLocale :to="`/evento/${point.slug}`" class="font-medium text-ink underline underline-offset-4 decoration-ink/40 hover:text-link">{{ point.title }}</NuxtLinkLocale>
          <p class="text-sm text-ink-muted">{{ point.island ?? '' }}</p>
        </li>
      </ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import type { MapPage } from '~/types/api'
import { CANARY_DEFAULT_VIEW, boundsToBbox } from '~/utils/map-geo'

const { t } = useI18n()

const bbox = ref(boundsToBbox({ south: CANARY_DEFAULT_VIEW.bounds[0][0], west: CANARY_DEFAULT_VIEW.bounds[0][1], north: CANARY_DEFAULT_VIEW.bounds[1][0], east: CANARY_DEFAULT_VIEW.bounds[1][1] }))

const { data: page } = await useFetch<MapPage>('/api/v1/events/map', {
  query: computed(() => ({ bbox: bbox.value })),
  key: 'map-points',
  watch: [bbox],
})

const points = computed(() => page.value?.items ?? [])
const truncated = computed(() => page.value?.truncated ?? false)

function onBoundsChange(next: string): void {
  bbox.value = next
}

// Geolocation runs only after an explicit tap, and the position is never stored.
const focus = ref<{ latitude: number; longitude: number; zoom: number } | null>(null)
const locateStatus = ref('')
function locate(): void {
  if (!('geolocation' in navigator)) {
    locateStatus.value = t('map.locateUnavailable')
    return
  }
  locateStatus.value = t('map.locating')
  navigator.geolocation.getCurrentPosition(
    (position) => {
      focus.value = { latitude: position.coords.latitude, longitude: position.coords.longitude, zoom: 11 }
      locateStatus.value = t('map.located')
    },
    () => {
      locateStatus.value = t('map.locateDenied')
    },
    { enableHighAccuracy: false, timeout: 10_000, maximumAge: 300_000 },
  )
}

useSiteSeo(() => ({ title: t('nav.map'), description: t('map.lead'), path: '/mapa' }))
</script>
