<script setup lang="ts">
import { computed } from 'vue'
import { kindNames, systemNames, onlineNames, type Device, type DeviceHints } from './api'
import DeviceReadings from './DeviceReadings.vue'
import DeviceIcon from './DeviceIcon.vue'

const props = defineProps<{ device: Device; display: DeviceHints }>()
const activity = computed(() => {
  const { device, display } = props
  if (device.online === 'offline') return display.offline
  const { status, using } = device.state
  if (device.online === 'online' && using === false && display.not_using) return display.not_using
  if (status) {
    const chars = Array.from(status)
    const limit = display.device_slice
    return limit && chars.length > limit ? chars.slice(0, limit).join('') + '…' : status
  }
  return device.online === 'online' && using === true ? display.in_use : ''
})
</script>
<template>
  <article class="device-card">
    <div class="device-icon" aria-hidden="true"><DeviceIcon :kind="device.kind" /></div>
    <div class="device-details">
      <h3>{{ device.name }}</h3>
      <p class="small muted">{{ systemNames[device.os] }} · {{ kindNames[device.kind] }}</p>
      <p v-if="activity" class="device-activity">{{ activity }}</p>
    </div>
    <div class="device-readings">
      <span class="badge" :class="{ green: device.online === 'online' }"
        ><i class="dot" />{{ onlineNames[device.online] }}</span
      >
      <p
        v-if="device.state.battery_percent != null || device.state.charging != null"
        class="small muted"
      >
        <span v-if="device.state.battery_percent != null"
          >电量 {{ device.state.battery_percent }}%</span
        >
        <span v-if="device.state.charging != null">
          {{ device.state.charging ? '充电中' : '未充电' }}</span
        >
      </p>
    </div>
    <DeviceReadings :device="device" />
  </article>
</template>
