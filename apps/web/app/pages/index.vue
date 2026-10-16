<template>
  <section aria-labelledby="home-title" class="space-y-4">
    <h1 id="home-title" class="font-display text-4xl font-semibold text-ocean">
      {{ t('site.name') }}
    </h1>
    <p class="max-w-prose text-lg text-ink-muted">{{ t('site.description') }}</p>
  </section>
</template>

<script setup lang="ts">
const { t } = useI18n()
useSeoMeta({
  title: () => t('site.name'),
  description: () => t('site.description'),
})
</script>
