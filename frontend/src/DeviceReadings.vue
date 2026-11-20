<script setup lang="ts">
import { computed } from 'vue'
import { metrics, type Device } from './api'
const props = defineProps<{ device: Device }>()
const readings = computed(() => {
  const { state, custom_fields } = props.device
  return [
    ...metrics
      .filter((f) => !custom_fields.some((c) => c.key === f.key))
      .map((f) => ({ ...f, unit: '%' })),
    ...(custom_fields ?? []).map((f) => ({ ...f, unit: '' })),
  ]
    .filter((f) => state[f.key] != null && !('role' in f && f.role === 'external_power'))
    .map((f) => {
      const value = state[f.key]
      return {
        key: f.key,
        name: f.name,
        value: typeof value === 'boolean' ? (value ? '是' : '否') : String(value) + f.unit,
      }
    })
})
</script>
<template>
  <dl v-if="readings.length" class="field-readings">
    <div v-for="field in readings" :key="field.key">
      <dt>{{ field.name }}</dt>
      <dd>{{ field.value }}</dd>
    </div>
  </dl>
</template>
