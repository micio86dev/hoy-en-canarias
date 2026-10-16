<template>
  <button
    type="button"
    :aria-pressed="favourite ? 'true' : 'false'"
    :aria-label="favourite ? t('favorites.remove') : t('favorites.add')"
    :title="favourite ? t('favorites.remove') : t('favorites.add')"
    :class="[
      'inline-flex items-center gap-2 rounded-pill border px-4 py-2 text-sm font-medium',
      favourite ? 'border-accent bg-accent/30 text-ink' : 'border-ocean text-ocean hover:bg-ocean-soft',
    ]"
    @click="toggle(slug)"
  >
    <span aria-hidden="true">{{ favourite ? '♥' : '♡' }}</span>
    <span>{{ favourite ? t('favorites.saved') : t('favorites.save') }}</span>
  </button>
</template>

<script setup lang="ts">
const props = defineProps<{ slug: string }>()
const { t } = useI18n()
const { load, isFavorite, toggle } = useFavorites()

onMounted(load)
const favourite = computed(() => isFavorite(props.slug))
</script>
