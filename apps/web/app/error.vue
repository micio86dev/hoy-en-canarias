<template>
  <div class="mx-auto max-w-prose space-y-4 py-16 text-center">
    <p class="text-sm font-medium uppercase tracking-wide text-link">{{ error.statusCode }}</p>
    <h1 class="font-display text-4xl font-semibold text-ocean">{{ t('error.title') }}</h1>
    <p class="text-ink-muted">{{ t('error.body') }}</p>
    <NuxtLinkLocale to="/" class="inline-block rounded-pill bg-ocean px-5 py-2 text-sm font-medium text-paper">{{ t('error.home') }}</NuxtLinkLocale>
  </div>
</template>

<script setup lang="ts">
import type { NuxtError } from '#app'

defineProps<{ error: NuxtError }>()
const { t } = useI18n()
useSeoMeta({ robots: 'noindex, nofollow', title: () => t('error.title') })
</script>
