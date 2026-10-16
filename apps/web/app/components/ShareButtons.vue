<template>
  <section aria-labelledby="share-title" class="space-y-3">
    <h2 id="share-title" class="font-semibold">{{ t('share.title') }}</h2>
    <ul class="flex flex-wrap gap-2" role="list">
      <li v-if="canNativeShare">
        <button type="button" :class="pill" @click="nativeShare">{{ t('share.device') }}</button>
      </li>
      <li v-for="target in targets" :key="target.id">
        <a :href="target.href" :target="target.id === 'email' ? undefined : '_blank'" :rel="target.id === 'email' ? undefined : 'noopener noreferrer nofollow'" :class="pill">
          {{ t(`share.${target.id}`) }}<span v-if="target.id !== 'email'" class="sr-only"> {{ t('event.opensNewTab') }}</span>
        </a>
      </li>
      <li>
        <button type="button" :class="pill" @click="copyLink">{{ t('share.copy') }}</button>
      </li>
    </ul>
    <p role="status" aria-live="polite" class="min-h-5 text-sm text-ink-muted">{{ status }}</p>
  </section>
</template>

<script setup lang="ts">
import { shareTargets } from '~/utils/share'

const props = defineProps<{ url: string; title: string; text?: string }>()
const { t } = useI18n()

const pill = 'inline-flex items-center rounded-pill border border-ocean px-4 py-2 text-sm font-medium text-ocean hover:bg-ocean-soft'
const targets = computed(() => shareTargets({ url: props.url, title: props.title, text: props.text }))
const canNativeShare = ref(false)
const status = ref('')

// Checked after mount so the server and the browser render the same markup.
onMounted(() => {
  canNativeShare.value = typeof navigator !== 'undefined' && typeof navigator.share === 'function'
})

async function nativeShare(): Promise<void> {
  try {
    await navigator.share({ title: props.title, text: props.text, url: props.url })
  } catch {
    // The visitor closed the sheet; nothing to report.
  }
}

async function copyLink(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.url)
    status.value = t('share.copied')
  } catch {
    status.value = t('share.copyFailed')
  }
}
</script>
