<template>
  <article class="flex flex-col overflow-hidden rounded-card bg-paper shadow-card ring-1 ring-ink/5">
    <NuxtLinkLocale
      v-if="showImage"
      :to="`/evento/${event.slug}`"
      tabindex="-1"
      aria-hidden="true"
      class="relative block aspect-[16/9] bg-sand"
    >
      <img
        :src="event.image_url ?? undefined"
        alt=""
        loading="lazy"
        decoding="async"
        referrerpolicy="no-referrer"
        class="h-full w-full object-cover"
        @error="imageFailed = true"
        ref="imageEl"
      />
    </NuxtLinkLocale>
    <NuxtLinkLocale
      v-else
      :to="`/evento/${event.slug}`"
      tabindex="-1"
      aria-hidden="true"
      data-testid="event-placeholder"
      class="relative flex aspect-[16/9] items-center justify-center"
      :class="fallbackClass"
    >
      <BrandMark class="h-12 w-auto opacity-80" />
      <span class="absolute inset-x-0 bottom-0 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide">
        {{ categoryLabel }}
      </span>
    </NuxtLinkLocale>
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
      <div class="pt-2">
        <FavoriteButton :slug="event.slug" />
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import type { EventSummary } from '~/types/api'
import { formatEventWhen } from '~/utils/dates'

const props = defineProps<{ event: EventSummary }>()
const imageFailed = ref(false)
const showImage = computed(() => Boolean(props.event.image_url) && !imageFailed.value)
watch(() => props.event.image_url, () => { imageFailed.value = false })

// A server-rendered image can fail before hydration, so its error event was already fired.
const imageEl = ref<HTMLImageElement | null>(null)
onMounted(() => {
  const image = imageEl.value
  if (image && image.complete && image.naturalWidth === 0) imageFailed.value = true
})
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
  // Without a source currency the amount is shown bare: never assume EUR.
  const amount = props.event.currency
    ? new Intl.NumberFormat(locale.value, { style: 'currency', currency: props.event.currency }).format(props.event.price_min)
    : new Intl.NumberFormat(locale.value, { maximumFractionDigits: 2 }).format(props.event.price_min)
  return t('event.priceFrom', { amount })
})

// Solid, token-based colour per category: no stock photo, no random gradient.
const FALLBACK_CLASS: Record<string, string> = {
  music: 'bg-ocean text-paper',
  festivals: 'bg-accent text-ink',
  markets: 'bg-sand text-ink',
  nature: 'bg-ocean-soft text-ocean',
  sports: 'bg-ink text-paper',
  family: 'bg-accent/60 text-ink',
  workshops: 'bg-sand text-ink',
  culture: 'bg-ocean/85 text-paper',
  nightlife: 'bg-ink text-paper',
  other: 'bg-sand text-ink',
}
const fallbackClass = computed(() => FALLBACK_CLASS[props.event.category] ?? FALLBACK_CLASS.other)
</script>
