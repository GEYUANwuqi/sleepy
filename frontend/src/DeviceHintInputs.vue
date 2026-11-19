<script setup lang="ts">
import type { DeviceHints } from './api'
defineProps<{ hints: DeviceHints }>()
</script>
<template>
  <div class="form-grid">
    <label>使用中提示<input v-model.trim="hints.in_use" maxlength="80" /></label>
    <label>在线但未使用时的提示<input v-model.trim="hints.not_using" maxlength="80" /></label>
  </div>
  <p class="hint">
    设备正在使用且没有上报活动描述时，显示使用中提示。设备上报未在使用时，显示未使用提示；留空则显示上报的活动描述。
  </p>
  <label>离线提示<input v-model.trim="hints.offline" maxlength="80" /></label>
  <p class="hint">超过离线阈值后，设备卡片用这句话替换活动和读数。留空可隐藏这句话。</p>
  <label
    >活动描述字数上限<input
      v-model.number="hints.device_slice"
      type="number"
      min="0"
      max="512"
      required
  /></label>
  <p class="hint">设为 0 时显示完整活动描述。</p>
</template>
