<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, type Snapshot } from './api'
import MusicCard from './MusicCard.vue'
import DeviceCard from './DeviceCard.vue'

const state = ref<Snapshot | null>(null)
const connected = ref(false)
const error = ref('')
let events: EventSource | undefined
let disposed = false
const online = computed(() => state.value?.devices.filter((d) => d.online === 'online').length || 0)
function accept(value: Snapshot) {
  state.value = value
  document.title = value.site.title
  document.documentElement.dataset.theme = value.site.theme
  error.value = ''
}
async function load() {
  try {
    accept(await api<Snapshot>('/api/status'))
  } catch (e) {
    error.value = (e as Error).message
  }
}
onMounted(async () => {
  await load()
  if (disposed) return
  if (!window.EventSource) {
    error.value = '当前浏览器不支持实时更新，请使用较新的浏览器。'
    return
  }
  events = new EventSource('/api/status/events')
  events.addEventListener('update', (e) => {
    accept(JSON.parse((e as MessageEvent).data))
    connected.value = true
  })
  events.onopen = () => {
    connected.value = true
  }
  events.onerror = () => {
    connected.value = false
  }
})
onUnmounted(() => {
  disposed = true
  events?.close()
})
</script>
<template>
  <main
    class="public-shell"
    :style="{ '--card-opacity': (state?.site.display.card_opacity ?? 90) + '%' }"
  >
    <img
      v-if="state?.site.display.background_url"
      :key="state.site.display.background_url"
      :src="state.site.display.background_url"
      class="public-wallpaper"
      alt=""
      referrerpolicy="no-referrer"
      @load="($event.target as HTMLImageElement).classList.add('loaded')"
    />
    <div class="public-content">
      <div v-if="error" class="notice error" role="alert">
        {{ error }} <button class="text-button" @click="load">重新获取</button>
      </div>
      <template v-if="state">
        <div class="status-card glass-card">
          <header class="public-header">
            <h1>{{ state.site.title }}</h1>
            <p v-if="state.site.description" class="muted">{{ state.site.description }}</p>
          </header>
          <section
            class="presence"
            aria-label="当前状态"
            :style="{ '--presence': state.presence.color }"
          >
            <h2>{{ state.presence.name }}</h2>
            <p v-if="state.presence.description" class="muted">{{ state.presence.description }}</p>
          </section>
          <section
            v-if="state.smtc || state.devices.length || state.site.display.empty_devices"
            class="device-section"
          >
            <div class="section-heading">
              <h2>设备</h2>
              <span v-if="state.devices.length" class="small muted"
                >{{ online }} / {{ state.devices.length }} 台在线</span
              >
            </div>
            <div v-if="state.smtc || state.devices.length" class="device-grid">
              <MusicCard v-if="state.smtc" :music="state.smtc" />
              <DeviceCard
                v-for="device in state.devices"
                :key="device.id"
                :device="device"
                :display="device.hints ?? state.site.display"
              />
            </div>
            <p v-else class="empty-devices">
              {{ state.site.display.empty_devices }}
            </p>
          </section>
        </div>
        <footer
          v-if="
            state.site.display.footer ||
            state.site.filings.length ||
            (state.site.display.link_url && state.site.display.link_text)
          "
          class="public-footer glass-card"
        >
          <p v-if="state.site.display.footer">{{ state.site.display.footer }}</p>
          <a
            v-if="state.site.display.link_url && state.site.display.link_text"
            :href="state.site.display.link_url"
            >{{ state.site.display.link_text }}</a
          >
          <nav v-if="state.site.filings.length" class="filing-links" aria-label="网站备案">
            <a
              v-for="filing in state.site.filings"
              :key="filing.kind"
              :href="filing.url"
              target="_blank"
              rel="noopener noreferrer"
            >
              <img
                v-if="filing.kind === 'police'"
                src="/assets/beian.png"
                width="16"
                height="18"
                alt=""
              />
              {{ filing.text }}
            </a>
          </nav>
        </footer>
        <p v-if="!connected && !error" class="connection-note" role="status">正在连接实时更新…</p>
      </template>
      <p v-else-if="!error" class="muted" role="status">正在获取状态…</p>
    </div>
  </main>
</template>
