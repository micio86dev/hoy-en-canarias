<template>
  <svg
    viewBox="0 0 48 32"
    fill="currentColor"
    aria-hidden="true"
    focusable="false"
    data-testid="brand-mark"
    :class="$attrs.class"
  >
    <!-- Eight islands, sized roughly like the archipelago, above two lines of Atlantic swell. -->
    <circle cx="7" cy="13" r="2.2" />
    <circle cx="12.5" cy="9" r="2.6" />
    <circle cx="18.5" cy="14.5" r="2.3" />
    <circle cx="24" cy="8" r="2.9" />
    <circle cx="29.5" cy="15" r="2.2" />
    <circle cx="35" cy="10.5" r="2.5" />
    <circle cx="40.5" cy="14.5" r="2" />
    <circle cx="45" cy="18" r="1.2" />
    <path d="M2 24.5q3-2.4 6 0t6 0 6 0 6 0 6 0 6 0 6 0 6 0" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
    <path d="M6 29q3-2.2 6 0t6 0 6 0 6 0 6 0 6 0" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" opacity="0.6" />
  </svg>
</template>

<script setup lang="ts">
defineOptions({ inheritAttrs: false })
</script>
