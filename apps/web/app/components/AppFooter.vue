<template>
  <footer class="border-t border-ink/10 bg-sand">
    <div class="mx-auto flex max-w-[var(--container-page)] flex-col gap-4 px-4 py-8 text-sm sm:px-6">
      <p class="max-w-prose text-ink-muted">{{ t('footer.notice') }}</p>
      <nav :aria-label="t('nav.primary')" class="flex flex-wrap gap-4 font-medium">
        <NuxtLinkLocale to="/acerca" class="hover:text-link">{{ t('footer.about') }}</NuxtLinkLocale>
        <NuxtLinkLocale to="/privacidad" class="hover:text-link">{{ t('footer.privacy') }}</NuxtLinkLocale>
      </nav>
    </div>
  </footer>
</template>

<script setup lang="ts">
const { t } = useI18n()
</script>
