<template>
  <form class="flex flex-col gap-6" :aria-label="t('search.filters')" @submit.prevent>
    <div class="flex flex-col gap-2">
      <label for="filter-island" class="text-sm font-medium">{{ t('search.island') }}</label>
      <select id="filter-island" :value="filters.island ?? ''" class="rounded-card border border-ink/20 bg-paper px-3 py-2" @change="setIsland">
        <option value="">{{ t('search.allIslands') }}</option>
        <option v-for="island in ISLANDS" :key="island" :value="island">{{ island }}</option>
      </select>
    </div>

    <div class="flex flex-col gap-2">
      <label for="filter-category" class="text-sm font-medium">{{ t('search.category') }}</label>
      <select id="filter-category" :value="filters.category ?? ''" class="rounded-card border border-ink/20 bg-paper px-3 py-2" @change="setCategory">
        <option value="">{{ t('search.allCategories') }}</option>
        <option v-for="category in CATEGORIES" :key="category" :value="category">{{ t(`categories.${category}`) }}</option>
      </select>
    </div>

    <fieldset class="flex flex-col gap-2">
      <legend class="mb-2 text-sm font-medium">{{ t('search.date') }}</legend>
      <div class="flex flex-wrap gap-2">
        <button
          v-for="option in DATE_OPTIONS"
          :key="option.value || 'any'"
          type="button"
          class="rounded-pill border border-ink/20 px-3 py-1.5 text-sm aria-pressed:border-ocean aria-pressed:bg-ocean aria-pressed:text-paper"
          :aria-pressed="(filters.preset ?? '') === option.value ? 'true' : 'false'"
          @click="setPreset(option.value)"
        >
          {{ t(option.label) }}
        </button>
      </div>
    </fieldset>

    <label class="flex items-center gap-2 text-sm font-medium">
      <input type="checkbox" class="size-4 accent-ocean" :checked="filters.free === true" @change="setFree" />
      {{ t('search.free') }}
    </label>

    <div class="flex flex-col gap-2">
      <label for="filter-sort" class="text-sm font-medium">{{ t('search.sort') }}</label>
      <select id="filter-sort" :value="filters.sort ?? 'date'" class="rounded-card border border-ink/20 bg-paper px-3 py-2" @change="setSort">
        <option value="date">{{ t('search.sortDate') }}</option>
        <option value="relevance">{{ t('search.sortRelevance') }}</option>
        <option value="distance">{{ t('search.sortDistance') }}</option>
      </select>
    </div>

    <button type="button" class="w-fit text-sm font-medium text-link underline underline-offset-4" @click="$emit('clear')">
      {{ t('search.clear') }}
    </button>
  </form>
</template>

<script setup lang="ts">
import { CATEGORIES, ISLANDS, type Preset, type Sort } from '~/utils/constants'
import type { SearchFilters } from '~/utils/search-params'

const props = defineProps<{ filters: SearchFilters }>()
const emit = defineEmits<{ update: [filters: SearchFilters]; clear: [] }>()
const { t } = useI18n()

const DATE_OPTIONS: Array<{ value: Preset | ''; label: string }> = [
  { value: '', label: 'search.anyDate' },
  { value: 'today', label: 'search.today' },
  { value: 'tomorrow', label: 'search.tomorrow' },
  { value: 'weekend', label: 'search.weekend' },
]

function update(patch: Partial<SearchFilters>): void {
  emit('update', { ...props.filters, page: undefined, ...patch })
}

function setIsland(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  update({ island: value ? (value as SearchFilters['island']) : undefined })
}

function setCategory(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  update({ category: value ? (value as SearchFilters['category']) : undefined })
}

function setPreset(value: Preset | ''): void {
  update({ preset: value || undefined })
}

function setFree(event: Event): void {
  update({ free: (event.target as HTMLInputElement).checked || undefined })
}

function setSort(event: Event): void {
  update({ sort: (event.target as HTMLSelectElement).value as Sort })
}
</script>
