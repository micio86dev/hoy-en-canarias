<template>
  <div>
    <div
      ref="host"
      class="h-[60vh] min-h-80 w-full overflow-hidden rounded-card ring-1 ring-ink/10"
      role="region"
      :aria-label="t('map.regionLabel')"
    />
    <p v-if="loadFailed" class="mt-3 text-sm text-ink-muted" role="status">{{ t('map.unavailable') }}</p>
  </div>
</template>

<script setup lang="ts">
import type { Map as LeafletMap, LayerGroup } from 'leaflet'
import type { MapPoint } from '~/types/api'
import { CANARY_DEFAULT_VIEW, boundsToBbox, pointsWithCoordinates, type GeoBounds } from '~/utils/map-geo'
import { formatEventWhen } from '~/utils/dates'
import markerIconUrl from 'leaflet/dist/images/marker-icon.png'
import markerIcon2xUrl from 'leaflet/dist/images/marker-icon-2x.png'
import markerShadowUrl from 'leaflet/dist/images/marker-shadow.png'

// Client-only: Leaflet needs the DOM, and it is imported lazily so pages without a map stay light.
const props = defineProps<{
  points: MapPoint[]
  focus: { latitude: number; longitude: number; zoom: number } | null
}>()
const emit = defineEmits<{ boundsChange: [bbox: string] }>()
const { t, locale } = useI18n()
const localePath = useLocalePath()
const config = useRuntimeConfig()

const host = useTemplateRef<HTMLElement>('host')
let map: LeafletMap | null = null
let markers: LayerGroup | null = null
let leaflet: typeof import('leaflet') | null = null
const loadFailed = ref(false)
let moveTimer: ReturnType<typeof setTimeout> | undefined

function toBounds(value: { getSouth(): number; getWest(): number; getNorth(): number; getEast(): number }): GeoBounds {
  return { south: value.getSouth(), west: value.getWest(), north: value.getNorth(), east: value.getEast() }
}

function popupContent(point: MapPoint): HTMLElement {
  // Built with textContent, never innerHTML: titles come from external sources.
  const root = document.createElement('div')
  const title = document.createElement('strong')
  title.textContent = point.title
  const when = document.createElement('p')
  when.textContent = formatEventWhen(
    { startAt: point.start_at, endAt: null, startDate: point.start_date, endDate: null, allDay: point.start_at === null },
    locale.value,
  )
  const link = document.createElement('a')
  link.href = localePath(`/evento/${point.slug}`)
  link.textContent = t('map.openEvent')
  root.append(title, when, link)
  return root
}

function render(): void {
  if (!leaflet || !markers) return
  markers.clearLayers()
  for (const point of pointsWithCoordinates(props.points)) {
    const marker = leaflet.marker([point.latitude, point.longitude], { title: point.title, alt: point.title })
    marker.bindPopup(popupContent(point))
    markers.addLayer(marker)
  }
}

onMounted(async () => {
  if (!host.value) return
  try {
    await mountMap(host.value)
  } catch (error) {
    // Never swallow this silently: the list alternative still works, and the reason is logged.
    console.error('event map could not be loaded', error)
    loadFailed.value = true
  }
})

async function mountMap(container: HTMLElement): Promise<void> {
  const module = await import('leaflet')
  // leaflet.markercluster is a classic script that reads a global L. Expose a plain copy of the
  // module (ES module namespaces cannot be extended), then load the plugin against it.
  ;(window as unknown as { L: typeof module }).L = { ...module }
  await Promise.all([
    import('leaflet/dist/leaflet.css'),
    import('leaflet.markercluster/dist/MarkerCluster.css'),
    import('leaflet.markercluster/dist/MarkerCluster.Default.css'),
  ])
  await import('leaflet.markercluster')
  leaflet = (window as unknown as { L: typeof module }).L
  // Bundled marker images: Leaflet cannot find its default icons under Vite's asset handling.
  leaflet.Icon.Default.mergeOptions({ iconUrl: markerIconUrl, iconRetinaUrl: markerIcon2xUrl, shadowUrl: markerShadowUrl })

  map = leaflet.map(container, {
    center: CANARY_DEFAULT_VIEW.center,
    zoom: CANARY_DEFAULT_VIEW.zoom,
    scrollWheelZoom: false,
  })
  leaflet
    .tileLayer(config.public.tileUrl as string, { attribution: t('map.attribution'), maxZoom: 18 })
    .addTo(map)
  markers = leaflet.markerClusterGroup({ showCoverageOnHover: false })
  map.addLayer(markers)

  map.on('moveend', () => {
    clearTimeout(moveTimer)
    moveTimer = setTimeout(() => {
      if (map) emit('boundsChange', boundsToBbox(toBounds(map.getBounds())))
    }, 300)
  })
  emit('boundsChange', boundsToBbox(toBounds(map.getBounds())))
  render()
}

watch(
  () => props.points,
  () => render(),
)

watch(
  () => props.focus,
  (focus) => {
    if (focus && map) map.setView([focus.latitude, focus.longitude], focus.zoom)
  },
)

onBeforeUnmount(() => {
  clearTimeout(moveTimer)
  map?.remove()
  map = null
})
</script>

<style scoped>
/* Leaflet injects the attribution link; underline it so it is not told apart by colour alone. */
:deep(.leaflet-control-attribution a) {
  text-decoration: underline;
}
</style>

