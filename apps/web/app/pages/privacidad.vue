<template>
  <article class="max-w-prose space-y-6">
    <h1 class="font-display text-4xl font-semibold text-ocean">{{ t('privacy.title') }}</h1>
    <p>{{ t('privacy.intro') }}</p>
    <h2 class="font-display text-2xl font-semibold">{{ t('privacy.dataTitle') }}</h2>
    <ul class="list-disc space-y-2 pl-5">
      <li>{{ t('privacy.noAccount') }}</li>
      <li>{{ t('privacy.noLocation') }}</li>
      <li>{{ t('privacy.voice') }}</li>
      <li>{{ t('privacy.noTracking') }}</li>
    </ul>
    <p class="text-ink-muted">{{ t('privacy.updated') }}</p>
  </article>
</template>

<script setup lang="ts">
const { t } = useI18n()
useSiteSeo(() => ({ title: t('privacy.title'), description: t('privacy.intro'), path: '/privacidad' }))
</script>
