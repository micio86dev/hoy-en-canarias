<template>
  <form role="search" class="flex flex-col gap-4" @submit.prevent="submit">
    <label for="search-input" class="text-sm font-medium text-ink">{{ t('search.title') }}</label>
    <div class="flex flex-col gap-2 sm:flex-row">
      <input
        id="search-input"
        v-model="text"
        type="search"
        name="q"
        autocomplete="off"
        :placeholder="t('search.placeholder')"
        class="min-w-0 flex-1 rounded-card border border-ink/20 bg-paper px-4 py-3 text-lg text-ink placeholder:text-ink-muted/70 focus:border-focus"
      />
      <button
        type="submit"
        class="rounded-card bg-ocean px-6 py-3 text-base font-semibold text-paper hover:bg-ocean/90"
      >
        {{ t('search.submit') }}
      </button>
    </div>
    <VoiceSearch @recognized="onRecognized" />
  </form>
</template>

<script setup lang="ts">
import { parseVoiceQuery, type VoiceQuery } from '~/utils/voice-query'

const emit = defineEmits<{ search: [query: VoiceQuery] }>()
const props = withDefaults(defineProps<{ initialText?: string }>(), { initialText: '' })
const { t, locale } = useI18n()

const text = ref(props.initialText)

function parse(value: string): VoiceQuery {
  const voiceLocale = locale.value === 'en' || locale.value === 'it' ? locale.value : 'es'
  return parseVoiceQuery(value, { locale: voiceLocale, today: new Date() })
}

// Recognised speech only fills the field; the visitor reviews it and presses Search.
function onRecognized(transcript: string): void {
  text.value = transcript
}

function submit(): void {
  emit('search', parse(text.value))
}
</script>
