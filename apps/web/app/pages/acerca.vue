<template>
  <article class="max-w-prose space-y-6">
    <h1 class="font-display text-4xl font-semibold text-ocean">{{ t('about.title') }}</h1>
    <p>{{ t('about.body') }}</p>
    <p class="text-ink-muted">{{ t('about.sources') }}</p>
  </article>
</template>

<script setup lang="ts">
const { t } = useI18n()
useSiteSeo(() => ({ title: t('about.title'), description: t('about.body'), path: '/acerca' }))
</script>
