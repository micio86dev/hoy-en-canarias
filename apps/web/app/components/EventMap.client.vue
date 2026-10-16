<template>
  <div
    ref="host"
    class="h-[60vh] min-h-80 w-full overflow-hidden rounded-card ring-1 ring-ink/10"
    role="region"
    :aria-label="t('map.regionLabel')"
  />
</template>

<script setup lang="ts">
import type { Map as LeafletMap, LayerGroup } from 'leaflet'
import type { MapPoint } from '~/types/api'
import { CANARY_DEFAULT_VIEW, boundsToBbox, pointsWithCoordinates, type GeoBounds } from '~/utils/map-geo'
import { formatEventWhen } from '~/utils/dates'

// Client-only: Leaflet needs the DOM, and it is imported lazily so pages without a map stay light.
const props = defineProps<{
  points: MapPoint[]
  focus: { latitude: number; longitude: number; zoom: number } | null
}>()
const emit = defineEmits<{ boundsChange: [bbox: string] }>()
const { t, locale } = useI18n()
const localePath = useLocalePath()
const config = useRuntimeConfig()

const host = ref<HTMLElement | null>(null)
let map: LeafletMap | null = null
let markers: LayerGroup | null = null
let leaflet: typeof import('leaflet') | null = null
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
  leaflet = await import('leaflet')
  await Promise.all([
    import('leaflet/dist/leaflet.css'),
    import('leaflet.markercluster/dist/MarkerCluster.css'),
    import('leaflet.markercluster/dist/MarkerCluster.Default.css'),
  ])
  await import('leaflet.markercluster')

  map = leaflet.map(host.value, {
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
})

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
