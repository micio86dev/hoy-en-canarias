<template>
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
</template>

<script setup lang="ts">
const i18nHead = useLocaleHead()

useHead(() => ({
  htmlAttrs: { lang: i18nHead.value.htmlAttrs?.lang, dir: i18nHead.value.htmlAttrs?.dir },
  link: [
    // Event images are served by the source site; opening the connection early saves a round trip.
    { rel: 'preconnect', href: 'https://www.holaislascanarias.com', crossorigin: 'anonymous' },
    ...(i18nHead.value.link ?? []),
  ],
  meta: [...(i18nHead.value.meta ?? [])],
}))
</script>
