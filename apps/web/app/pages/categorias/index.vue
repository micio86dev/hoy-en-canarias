<template>
  <section class="space-y-8" aria-labelledby="categories-index-title">
    <h1 id="categories-index-title" class="font-display text-4xl font-semibold text-ocean">{{ t('nav.categories') }}</h1>
    <ul class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" role="list">
      <li v-for="category in categories?.items ?? []" :key="category.key">
        <NuxtLinkLocale :to="`/categorias/${category.key}`" class="block rounded-card bg-sand p-5 hover:bg-ocean-soft">
          <span class="font-display text-2xl font-semibold">{{ t(`categories.${category.key}`) }}</span>
          <span class="mt-1 block text-sm text-ink-muted">{{ t(countKey('home.upcomingCount', category.count), { count: category.count }) }}</span>
        </NuxtLinkLocale>
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { countKey } from '~/utils/plural'
import type { CountList } from '~/types/api'

const { t } = useI18n()
const { data: categories } = await useAsyncData('categories-index', () => $fetch<CountList>('/api/v1/categories'))
useSiteSeo(() => ({ title: t('nav.categories'), description: t('site.description'), path: '/categorias' }))
</script>
