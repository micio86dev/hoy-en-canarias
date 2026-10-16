<template>
  <section class="space-y-8" aria-labelledby="islands-title">
    <h1 id="islands-title" class="font-display text-4xl font-semibold text-ocean">{{ t('nav.islands') }}</h1>
    <ul class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" role="list">
      <li v-for="island in islands?.items ?? []" :key="island.key">
        <NuxtLinkLocale :to="`/islas/${islandToSegment(island.key)}`" class="block rounded-card bg-sand p-5 hover:bg-ocean-soft">
          <span class="font-display text-2xl font-semibold">{{ island.key }}</span>
          <span class="mt-1 block text-sm text-ink-muted">{{ t(countKey('home.upcomingCount', island.count), { count: island.count }) }}</span>
        </NuxtLinkLocale>
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { countKey } from '~/utils/plural'
import type { CountList } from '~/types/api'
import { islandToSegment } from '~/utils/route-segments'

const { t } = useI18n()
const { data: islands } = await useAsyncData('islands-index', () => $fetch<CountList>('/api/v1/islands'))
useSiteSeo(() => ({ title: t('nav.islands'), description: t('site.description'), path: '/islas' }))
</script>
