<template>
  <div class="mx-auto max-w-prose space-y-8">
    <header class="space-y-3">
      <h1 class="font-display text-4xl font-semibold text-ocean">{{ t('submit.title') }}</h1>
      <p class="text-lg text-ink-muted">{{ t('submit.intro') }}</p>
    </header>

    <div v-if="done" class="rounded-card bg-sand p-6" role="status">
      <p class="font-semibold">{{ t('submit.received') }}</p>
      <p class="mt-2 text-ink-muted">{{ t('submit.receivedBody') }}</p>
    </div>

    <form v-else class="space-y-6" novalidate @submit.prevent="send">
      <div class="space-y-2">
        <label for="sub-title" class="font-medium">{{ t('submit.fields.title') }} *</label>
        <input id="sub-title" v-model="draft.title" type="text" maxlength="160" required :aria-invalid="!!errors.title" aria-describedby="sub-title-err" class="w-full rounded-card border border-ink/20 bg-paper px-4 py-3" />
        <p v-if="errors.title" id="sub-title-err" class="text-sm text-red-700">{{ t('submit.errors.title') }}</p>
      </div>

      <div class="grid gap-6 sm:grid-cols-2">
        <div class="space-y-2">
          <label for="sub-date" class="font-medium">{{ t('submit.fields.startDate') }} *</label>
          <input id="sub-date" v-model="draft.startDate" type="date" required :aria-invalid="!!errors.startDate" aria-describedby="sub-date-err" class="w-full rounded-card border border-ink/20 bg-paper px-4 py-3" />
          <p v-if="errors.startDate" id="sub-date-err" class="text-sm text-red-700">{{ t('submit.errors.startDate') }}</p>
        </div>
        <div class="space-y-2">
          <label for="sub-island" class="font-medium">{{ t('submit.fields.island') }} *</label>
          <select id="sub-island" v-model="draft.island" required :aria-invalid="!!errors.island" aria-describedby="sub-island-err" class="w-full rounded-card border border-ink/20 bg-paper px-4 py-3">
            <option value="">{{ t('submit.fields.chooseIsland') }}</option>
            <option v-for="island in ISLANDS" :key="island" :value="island">{{ island }}</option>
          </select>
          <p v-if="errors.island" id="sub-island-err" class="text-sm text-red-700">{{ t('submit.errors.island') }}</p>
        </div>
      </div>

      <div class="grid gap-6 sm:grid-cols-2">
        <div class="space-y-2">
          <label for="sub-municipality" class="font-medium">{{ t('submit.fields.municipality') }}</label>
          <input id="sub-municipality" v-model="draft.municipality" type="text" maxlength="120" class="w-full rounded-card border border-ink/20 bg-paper px-4 py-3" />
        </div>
        <div class="space-y-2">
          <label for="sub-venue" class="font-medium">{{ t('submit.fields.venue') }}</label>
          <input id="sub-venue" v-model="draft.venue" type="text" maxlength="160" class="w-full rounded-card border border-ink/20 bg-paper px-4 py-3" />
        </div>
      </div>

      <div class="space-y-2">
        <label for="sub-url" class="font-medium">{{ t('submit.fields.url') }}</label>
        <input id="sub-url" v-model="draft.url" type="url" maxlength="500" placeholder="https://" :aria-invalid="!!errors.url" aria-describedby="sub-url-err" class="w-full rounded-card border border-ink/20 bg-paper px-4 py-3" />
        <p v-if="errors.url" id="sub-url-err" class="text-sm text-red-700">{{ t('submit.errors.url') }}</p>
      </div>

      <div class="space-y-2">
        <label for="sub-description" class="font-medium">{{ t('submit.fields.description') }}</label>
        <textarea id="sub-description" v-model="draft.description" rows="5" maxlength="2000" class="w-full rounded-card border border-ink/20 bg-paper px-4 py-3" />
        <p class="text-sm text-ink-muted">{{ t('submit.privacy') }}</p>
        <VoiceSearch @recognized="appendDescription" />
      </div>

      <!-- Honeypot: humans never see or fill this field; simple bots often do. -->
      <div class="absolute -left-[9999px] h-0 overflow-hidden" aria-hidden="true">
        <label for="sub-website">Website</label>
        <input id="sub-website" v-model="draft.honeypot" type="text" tabindex="-1" autocomplete="off" />
      </div>

      <p v-if="failed" role="alert" class="text-sm text-red-700">{{ t('submit.failed') }}</p>

      <button type="submit" :disabled="sending" class="rounded-card bg-ocean px-6 py-3 font-semibold text-paper disabled:opacity-60">
        {{ sending ? t('submit.sending') : t('submit.send') }}
      </button>
    </form>
  </div>
</template>

<script setup lang="ts">
import { ISLANDS } from '~/utils/constants'
import { validateSubmission, type SubmissionDraft } from '~/utils/submission'

const { t } = useI18n()
const draft = reactive<SubmissionDraft>({
  title: '',
  description: '',
  startDate: '',
  island: '',
  municipality: '',
  venue: '',
  url: '',
  honeypot: '',
})
const errors = reactive<Record<string, string>>({})
const sending = ref(false)
const done = ref(false)
const failed = ref(false)

function appendDescription(transcript: string): void {
  draft.description = [draft.description.trim(), transcript].filter(Boolean).join(' ').slice(0, 2000)
}

async function send(): Promise<void> {
  failed.value = false
  for (const key of Object.keys(errors)) delete errors[key]
  const result = validateSubmission(draft, new Date())
  Object.assign(errors, result.errors)
  if (!result.payload) return

  sending.value = true
  try {
    await $fetch('/api/v1/submissions', { method: 'POST', body: result.payload })
    done.value = true
  } catch {
    failed.value = true
  } finally {
    sending.value = false
  }
}

useSiteSeo(() => ({
  title: t('submit.title'),
  description: t('submit.intro'),
  path: '/enviar',
}))
</script>
