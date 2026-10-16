<template>
  <div class="flex min-h-dvh flex-col">
    <a
      href="#main"
      class="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-ink focus:px-4 focus:py-2 focus:text-paper"
    >
      {{ t('nav.skipToContent') }}
    </a>
    <AppHeader />
    <main id="main" tabindex="-1" class="mx-auto w-full max-w-[var(--container-page)] flex-1 px-4 py-8 sm:px-6">
      <slot />
    </main>
    <AppFooter />
  </div>
</template>

<script setup lang="ts">
const { t } = useI18n()
</script>
