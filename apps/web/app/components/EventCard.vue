<template>
  <article class="flex flex-col overflow-hidden rounded-card bg-paper shadow-card ring-1 ring-ink/5">
    <div class="relative aspect-[16/9] bg-ocean-soft" :style="fallbackStyle" aria-hidden="true">
      <img
        v-if="event.image_url"
        :src="event.image_url"
        alt=""
        loading="lazy"
        decoding="async"
        class="h-full w-full object-cover"
      />
      <span
        v-else
        class="absolute inset-x-0 bottom-0 bg-ocean/80 px-3 py-1 text-xs font-medium uppercase tracking-wide text-paper"
      >
        {{ categoryLabel }}
      </span>
    </div>
    <div class="flex flex-1 flex-col gap-2 p-4">
      <p class="text-sm font-medium text-link">{{ when }}</p>
      <h3 class="font-display text-xl font-semibold leading-snug text-ink">
        <NuxtLinkLocale :to="`/evento/${event.slug}`" class="hover:text-link">{{ event.title }}</NuxtLinkLocale>
      </h3>
      <p class="text-sm text-ink-muted">{{ place }}</p>
      <div class="mt-auto flex flex-wrap items-center gap-2 pt-2 text-xs font-medium">
        <span class="rounded-pill bg-sand px-2 py-1 text-ink">{{ categoryLabel }}</span>
        <span
          v-if="event.is_free === true"
          class="rounded-pill bg-ocean-soft px-2 py-1 text-ocean"
        >{{ t('event.free') }}</span>
        <span v-else-if="priceText" class="rounded-pill bg-ocean-soft px-2 py-1 text-ocean">{{ priceText }}</span>
        <span v-if="event.stale" class="rounded-pill bg-accent/30 px-2 py-1 text-ink">{{ t('event.stale') }}</span>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import type { EventSummary } from '~/types/api'
import { formatEventWhen } from '~/utils/dates'

const props = defineProps<{ event: EventSummary }>()
const { t, locale } = useI18n()

const when = computed(() =>
  formatEventWhen(
    {
      startAt: props.event.start_at,
      endAt: props.event.end_at,
      startDate: props.event.start_date,
      endDate: props.event.end_date,
      allDay: props.event.all_day,
    },
    locale.value,
  ),
)

const categoryLabel = computed(() => t(`categories.${props.event.category}`))

const place = computed(() =>
  [props.event.venue_name, props.event.municipality, props.event.island].filter(Boolean).join(' · ') ||
  t('event.placeUnknown'),
)

const priceText = computed(() => {
  if (props.event.price_min === null) return ''
  const currency = props.event.currency ?? 'EUR'
  return t('event.priceFrom', {
    amount: new Intl.NumberFormat(locale.value, { style: 'currency', currency }).format(props.event.price_min),
  })
})

// A deterministic CSS fallback instead of a stock photo that could misrepresent the event.
const fallbackStyle = computed(() => {
  const hue = [...props.event.slug].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 60
  return { background: `linear-gradient(135deg, hsl(${195 + hue} 45% 30%), hsl(${20 + hue} 80% 70%))` }
})
</script>
