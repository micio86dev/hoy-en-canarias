<template>
  <div v-if="supported" class="flex flex-col gap-2">
    <button
      type="button"
      class="inline-flex w-fit items-center gap-2 rounded-pill border border-ocean px-4 py-2 text-sm font-medium text-ocean hover:bg-ocean-soft aria-pressed:bg-ocean aria-pressed:text-paper"
      :aria-pressed="listening ? 'true' : 'false'"
      @click="listening ? stop() : start()"
    >
      {{ listening ? t('search.voiceStop') : t('search.voiceStart') }}
    </button>
    <p aria-live="polite" class="text-sm text-ink-muted">{{ status }}</p>
  </div>
</template>

<script setup lang="ts">
// Progressive enhancement: rendered only where the browser exposes speech recognition.
// Audio is streamed by the browser's own service; nothing is recorded or stored here.
interface RecognitionResult {
  transcript: string
}
interface RecognitionEvent {
  results: ArrayLike<ArrayLike<RecognitionResult>>
}
interface RecognitionErrorEvent {
  error: string
}
interface RecognitionLike {
  lang: string
  interimResults: boolean
  maxAlternatives: number
  onresult: ((event: RecognitionEvent) => void) | null
  onerror: ((event: RecognitionErrorEvent) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
}
type RecognitionConstructor = new () => RecognitionLike

const BCP47: Record<string, string> = { es: 'es-ES', en: 'en-GB', it: 'it-IT' }

const emit = defineEmits<{ recognized: [text: string] }>()
const { t, locale } = useI18n()

const supported = ref(false)
const listening = ref(false)
const status = ref('')
let recognition: RecognitionLike | null = null

function constructor(): RecognitionConstructor | undefined {
  const scope = window as unknown as {
    SpeechRecognition?: RecognitionConstructor
    webkitSpeechRecognition?: RecognitionConstructor
  }
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition
}

onMounted(() => {
  supported.value = constructor() !== undefined
})

function start(): void {
  const Recognition = constructor()
  if (!Recognition) {
    status.value = t('search.voiceUnsupported')
    return
  }
  recognition = new Recognition()
  recognition.lang = BCP47[locale.value] ?? 'es-ES'
  recognition.interimResults = false
  recognition.maxAlternatives = 1
  recognition.onresult = (event) => {
    const transcript = event.results[0]?.[0]?.transcript?.trim() ?? ''
    if (transcript) {
      emit('recognized', transcript)
      status.value = t('search.voiceReview')
    }
  }
  recognition.onerror = (event) => {
    status.value = event.error === 'not-allowed' ? t('search.voiceDenied') : t('search.voiceError')
  }
  recognition.onend = () => {
    listening.value = false
  }
  status.value = t('search.voiceListening')
  listening.value = true
  recognition.start()
}

function stop(): void {
  recognition?.stop()
  listening.value = false
}

onBeforeUnmount(() => stop())
</script>
