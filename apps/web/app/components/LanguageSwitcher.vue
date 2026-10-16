<template>
  <nav :aria-label="t('language.label')" class="flex items-center gap-1 text-sm">
    <NuxtLinkLocale
      v-for="item in availableLocales"
      :key="item.code"
      :to="switchLocalePath(item.code)"
      :lang="item.language"
      :aria-current="item.code === locale ? 'true' : undefined"
      class="rounded-pill px-2 py-1 font-medium text-ink-muted hover:text-ink aria-[current=true]:bg-ocean aria-[current=true]:text-paper"
    >
      {{ t(`language.${item.code}`) }}
    </NuxtLinkLocale>
  </nav>
</template>

<script setup lang="ts">
const { t, locale, locales } = useI18n()
const switchLocalePath = useSwitchLocalePath()

const availableLocales = computed(() =>
  locales.value.map((item) => ({ code: item.code, language: item.language ?? item.code })),
)
</script>
