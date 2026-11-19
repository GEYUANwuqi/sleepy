<script setup lang="ts">
import type { Kind } from './api'
defineProps<{ kind: Kind }>()
</script>
<template>
  <svg
    viewBox="0 0 32 32"
    fill="none"
    stroke="currentColor"
    stroke-width="1.5"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <template v-if="kind === 'phone' || kind === 'tablet'">
      <rect
        :x="kind === 'phone' ? 9 : 5"
        y="3"
        :width="kind === 'phone' ? 14 : 22"
        height="26"
        rx="3"
      />
      <path d="M14 25h4" />
    </template>
    <template v-else-if="kind === 'laptop'">
      <rect x="6" y="6" width="20" height="15" rx="2" />
      <path d="m6 21-3 5h26l-3-5M13 24h6" />
    </template>
    <template v-else-if="kind === 'server'">
      <rect x="6" y="4" width="20" height="24" rx="2" />
      <path d="M6 12h20M6 20h20M10 8h1M10 16h1M10 24h1M18 8h4M18 16h4M18 24h4" />
    </template>
    <template v-else>
      <rect x="3" y="5" width="26" height="18" rx="2" />
      <path d="M16 23v5M10 28h12" />
    </template>
  </svg>
</template>
