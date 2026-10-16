<template>
  <header class="border-b border-ink/10 bg-paper">
    <div class="mx-auto flex max-w-[var(--container-page)] flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6">
      <NuxtLinkLocale to="/" class="flex items-center gap-2 font-display text-2xl font-semibold text-ocean">
        <BrandMark class="h-6 w-auto" />
        {{ t('site.name') }}
      </NuxtLinkLocale>
      <nav :aria-label="t('nav.primary')" class="flex flex-wrap items-center gap-4 text-sm font-medium">
        <NuxtLinkLocale to="/buscar" class="hover:text-link">{{ t('nav.search') }}</NuxtLinkLocale>
        <NuxtLinkLocale to="/islas" class="hover:text-link">{{ t('nav.islands') }}</NuxtLinkLocale>
        <NuxtLinkLocale to="/categorias" class="hover:text-link">{{ t('nav.categories') }}</NuxtLinkLocale>
        <NuxtLinkLocale to="/mapa" class="hover:text-link">{{ t('nav.map') }}</NuxtLinkLocale>
      </nav>
      <LanguageSwitcher />
    </div>
  </header>
</template>

<script setup lang="ts">
const { t } = useI18n()
</script>
