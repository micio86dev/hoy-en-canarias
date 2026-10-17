<template>
  <div class="flex flex-col gap-2">
    <button
      type="button"
      :aria-pressed="attending ? 'true' : 'false'"
      :disabled="pending"
      :class="[
        'inline-flex items-center gap-2 rounded-pill border px-4 py-2 text-sm font-medium disabled:opacity-60',
        attending ? 'border-accent bg-accent/30 text-ink' : 'border-ocean text-ocean hover:bg-ocean-soft',
      ]"
      @click="toggle"
    >
      <span aria-hidden="true">{{ attending ? '✓' : '+' }}</span>
      <span>{{ attending ? t('attendance.joined') : t('attendance.join') }}</span>
    </button>
    <p class="text-sm text-ink-muted" role="status">
      {{ t('attendance.count', { count: attendees }) }}
      <span v-if="failed" class="block text-red-700">{{ t('attendance.error') }}</span>
    </p>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{ slug: string; initialCount: number }>()
const { t } = useI18n()
const { state, pending, failed, load, toggle } = useAttendance(props.slug)

onMounted(load)
const attendees = computed(() => state.value?.attendees ?? props.initialCount)
const attending = computed(() => state.value?.attending ?? false)
</script>
