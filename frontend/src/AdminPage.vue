<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import {
  api,
  ApiError,
  kindNames,
  systemNames,
  onlineNames,
  powerText,
  type Auth,
  type Snapshot,
  type Settings,
  type SettingsToken,
  type Device,
  type DeviceConfig,
  type Statistics,
} from './api'
import MusicCard from './MusicCard.vue'
import DeviceIcon from './DeviceIcon.vue'
import DeviceEditor from './DeviceEditor.vue'
import DeviceReadings from './DeviceReadings.vue'

const route = useRoute()
const section = computed(() => String(route.params.section || 'devices'))
const auth = ref<Auth | null>(null),
  snapshot = ref<Snapshot | null>(null),
  settings = ref<Settings | null>(null)
const statistics = ref<Statistics | null>(null),
  runtime = ref<Record<string, unknown>>({})
const password = ref(''),
  currentPassword = ref(''),
  newPassword = ref(''),
  confirmPassword = ref('')
const busy = ref(false),
  error = ref(''),
  success = ref(''),
  loading = ref(true)
const edit = ref<Device | null | undefined>(undefined),
  freshKey = ref('')
const keyOwner = ref('设备')
const tokenName = ref('')
const tokens = ref<SettingsToken[]>([])
const credentialLabel = computed(() => (keyOwner.value === 'API' ? 'API Token' : 'Key'))
const manual = ref(''),
  duration = ref('3600'),
  statCategory = ref(''),
  proxyList = ref(''),
  newLabelId = ref(''),
  newLabelName = ref('')
let events: EventSource | undefined
let expiration: ReturnType<typeof setTimeout> | undefined
let disposed = false
const csrf = () => auth.value?.csrf || ''

async function guarded(work: () => Promise<void>) {
  busy.value = true
  error.value = ''
  success.value = ''
  try {
    await work()
  } catch (e) {
    error.value = (e as Error).message
    if (e instanceof ApiError && e.status === 401 && auth.value?.authenticated) await initialize()
  } finally {
    busy.value = false
  }
}
async function loadStatistics() {
  statistics.value = await api<Statistics>(
    '/api/admin/statistics' +
      (statCategory.value ? `?category=${encodeURIComponent(statCategory.value)}` : ''),
  )
}
async function loadDashboard() {
  const [state, config, tokenList] = await Promise.all([
    api<Snapshot>('/api/admin/devices'),
    api<{ settings: Settings; runtime: Record<string, unknown> }>('/api/admin/settings'),
    api<SettingsToken[]>('/api/admin/tokens'),
  ])
  tokens.value = tokenList
  snapshot.value = state
  settings.value = config.settings
  runtime.value = config.runtime
  proxyList.value = settings.value.trusted_proxies.join(', ')
  savedSettings = settingsJson.value
  manual.value = state.manual?.status || ''
  await loadStatistics()
  if (disposed) return
  events?.close()
  events = new EventSource('/api/admin/events')
  events.addEventListener('update', (event) => {
    const next: Snapshot = JSON.parse((event as MessageEvent).data)
    const previousStatus = snapshot.value?.manual?.status || ''
    if (manual.value === previousStatus) manual.value = next.manual?.status || ''
    snapshot.value = next
  })
  events.addEventListener('session-expired', () => {
    void initialize()
  })
}
async function initialize() {
  events?.close()
  clearTimeout(expiration)
  clearTimeout(saveTimer)
  saveMessage.value = ''
  saveError.value = ''
  snapshot.value = null
  settings.value = null
  freshKey.value = ''
  tokens.value = []
  tokenName.value = ''
  edit.value = undefined
  try {
    auth.value = await api<Auth>('/api/admin/auth')
    if (auth.value.authenticated) {
      expiration = setTimeout(
        () => {
          void initialize()
        },
        Math.max(0, auth.value.expires * 1000 - Date.now()) + 50,
      )
      if (!auth.value.must_change) await loadDashboard()
    }
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}
async function login() {
  await guarded(async () => {
    auth.value = await api<Auth>('/api/admin/login', 'POST', { password: password.value }, csrf())
    currentPassword.value = password.value
    password.value = ''
    await initialize()
  })
}
async function logout() {
  await guarded(async () => {
    await flushSettings()
    await api('/api/admin/logout', 'POST', undefined, csrf())
    password.value = ''
    currentPassword.value = ''
    await initialize()
  })
}
async function changePassword() {
  await guarded(async () => {
    if (newPassword.value !== confirmPassword.value) throw new Error('两次输入的新密码不一致')
    await api(
      '/api/admin/password',
      'POST',
      { current_password: currentPassword.value, new_password: newPassword.value },
      csrf(),
    )
    currentPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
    await initialize()
    success.value = '密码已修改，请使用新密码登录。'
  })
}
async function saveDevice(config: DeviceConfig) {
  await guarded(async () => {
    if (edit.value) await api(`/api/admin/devices/${edit.value.id}`, 'PUT', config, csrf())
    else {
      const value = await api<{ key: string }>('/api/admin/devices', 'POST', config, csrf())
      keyOwner.value = '设备'
      freshKey.value = value.key
    }
    edit.value = undefined
    snapshot.value = await api<Snapshot>('/api/admin/devices')
    success.value = '设备已保存。'
  })
}
async function removeDevice(device: Device) {
  if (!window.confirm(`删除“${device.name}”？该设备的 Key 将立即失效。`)) return
  await guarded(async () => {
    await api(`/api/admin/devices/${device.id}`, 'DELETE', undefined, csrf())
    snapshot.value = await api<Snapshot>('/api/admin/devices')
  })
}
async function rotateKey(device: Device) {
  if (!window.confirm(`重置“${device.name}”的 Key？客户端需要换用新 Key。`)) return
  await guarded(async () => {
    keyOwner.value = '设备'
    freshKey.value = (
      await api<{ key: string }>(`/api/admin/devices/${device.id}/key`, 'POST', undefined, csrf())
    ).key
  })
}
async function changeSmtc(enabled: boolean, rotate = false) {
  if (rotate && !window.confirm('重置 SMTC Key？原 Key 将立即失效。')) return
  await guarded(async () => {
    const result = rotate
      ? await api<{ key: string }>('/api/admin/smtc/key', 'POST', undefined, csrf())
      : await api<{ key: string | null }>('/api/admin/smtc', 'PUT', { enabled }, csrf())
    if (result.key) {
      keyOwner.value = 'SMTC'
      freshKey.value = result.key
    }
    snapshot.value = await api<Snapshot>('/api/admin/devices')
  })
}
const settingsForm = ref<HTMLFormElement>()
const saveMessage = ref('')
const saveError = ref('')
const settingsJson = computed(() => {
  const value = settings.value
  if (!value) return ''
  return JSON.stringify({
    ...value,
    trusted_proxies: proxyList.value
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean),
    ssl_certfile: value.ssl_certfile || null,
    ssl_keyfile: value.ssl_keyfile || null,
  })
})
let savedSettings = ''
let saveTimer: ReturnType<typeof setTimeout> | undefined
let saveQueue = Promise.resolve()

// Send only edited values so a concurrent API update to another setting survives autosave.
function changedSettings(before: Record<string, unknown>, after: Record<string, unknown>) {
  const changed: Record<string, unknown> = Object.create(null)
  for (const [key, value] of Object.entries(after)) {
    const old = before[key]
    if (JSON.stringify(old) === JSON.stringify(value)) continue
    if (
      value &&
      old &&
      typeof value === 'object' &&
      typeof old === 'object' &&
      !Array.isArray(value)
    ) {
      changed[key] = changedSettings(
        old as Record<string, unknown>,
        value as Record<string, unknown>,
      )
    } else {
      changed[key] = value
    }
  }
  return changed
}
function flushSettings() {
  clearTimeout(saveTimer)
  const payload = settingsJson.value
  const session = csrf()
  if (!payload || !settingsForm.value) return saveQueue
  const invalid = settingsForm.value.querySelector<HTMLInputElement>(':invalid')
  if (invalid) {
    const label = invalid.labels?.[0]?.textContent?.trim() || '设置'
    saveError.value = `${label}：${invalid.validationMessage}`
    saveMessage.value = ''
    return saveQueue
  }
  saveQueue = saveQueue.then(async () => {
    if (disposed || csrf() !== session || payload !== settingsJson.value) return
    if (payload === savedSettings) {
      saveError.value = ''
      saveMessage.value = '已自动保存'
      return
    }
    saveError.value = ''
    saveMessage.value = '正在保存…'
    try {
      const patch = changedSettings(JSON.parse(savedSettings), JSON.parse(payload))
      await api('/api/admin/settings', 'PATCH', patch, session)
      if (csrf() !== session) return
      savedSettings = payload
      if (payload === settingsJson.value) saveMessage.value = '已自动保存'
    } catch (e) {
      if (csrf() !== session || payload !== settingsJson.value) return
      saveMessage.value = ''
      saveError.value = (e as Error).message
      if (e instanceof ApiError && e.status === 401) await initialize()
    }
  })
  return saveQueue
}
watch(
  settingsJson,
  (value) => {
    clearTimeout(saveTimer)
    if (!settingsForm.value || !value) return
    saveMessage.value = '等待保存…'
    saveError.value = ''
    saveTimer = setTimeout(flushSettings, 600)
  },
  { flush: 'post' },
)
onBeforeRouteUpdate(async () => {
  await flushSettings()
})
onBeforeRouteLeave(async () => {
  await flushSettings()
})
async function saveStatus() {
  await guarded(async () => {
    await api(
      '/api/admin/status',
      'PUT',
      {
        status: manual.value || null,
        duration: duration.value === 'forever' ? null : Number(duration.value),
      },
      csrf(),
    )
    snapshot.value = await api<Snapshot>('/api/admin/devices')
    success.value = manual.value ? '手动状态已生效。' : '已恢复自动状态。'
  })
}
function addLabel() {
  const id = newLabelId.value.trim()
  if (!/^[a-zA-Z_][a-zA-Z0-9_]{0,31}$/.test(id)) {
    error.value = '状态标识应以英文字母或下划线开头，最多 32 位'
    return
  }
  if (!settings.value || !newLabelName.value.trim()) return
  if (id in settings.value.labels) {
    error.value = '该状态标识已被使用，请换一个。'
    return
  }
  if (Object.keys(settings.value.labels).length >= 30) {
    error.value = '最多可以设置 30 个状态。'
    return
  }
  error.value = ''
  settings.value.labels[id] = { name: newLabelName.value.trim(), description: '', color: '#448b70' }
  newLabelId.value = ''
  newLabelName.value = ''
}
async function createToken() {
  await guarded(async () => {
    const { token, ...info } = await api<SettingsToken & { token: string }>(
      '/api/admin/tokens',
      'POST',
      { name: tokenName.value },
      csrf(),
    )
    tokens.value.unshift(info)
    tokenName.value = ''
    keyOwner.value = 'API'
    freshKey.value = token
  })
}
async function revokeToken(item: SettingsToken) {
  if (!window.confirm(`撤销“${item.name}”？使用此 Token 的脚本将失去访问权限。`)) return
  await guarded(async () => {
    await api(`/api/admin/tokens/${item.id}`, 'DELETE', undefined, csrf())
    tokens.value = tokens.value.filter((t) => t.id !== item.id)
    success.value = 'API Token 已撤销。'
  })
}
async function copyKey() {
  await guarded(async () => {
    if (!navigator.clipboard)
      throw new Error(`当前地址不支持一键复制，请选中 ${credentialLabel.value} 手动复制。`)
    await navigator.clipboard.writeText(freshKey.value)
    success.value = `${credentialLabel.value} 已复制。`
  })
}
onMounted(() => {
  document.title = '管理面板'
  document.documentElement.dataset.theme = 'system'
  void initialize()
})
onUnmounted(() => {
  disposed = true
  clearTimeout(saveTimer)
  events?.close()
  clearTimeout(expiration)
})
</script>
<template>
  <main v-if="loading" class="login-shell"><p>正在连接管理面板…</p></main>
  <main v-else-if="!auth?.authenticated || auth.must_change" class="login-shell">
    <section class="login-card">
      <h1>{{ auth?.must_change ? '设置你的密码' : '管理员登录' }}</h1>
      <p class="muted">
        {{ auth?.must_change ? '首次登录需要先更换临时密码。' : '登录后管理设备与公开状态。' }}
      </p>
      <div v-if="error" class="notice error" role="alert">{{ error }}</div>
      <div v-if="success" class="notice" role="status">{{ success }}</div>
      <form v-if="!auth?.authenticated" @submit.prevent="login">
        <label
          >管理员密码<input
            v-model="password"
            type="password"
            required
            autocomplete="current-password"
            autofocus /></label
        ><button class="primary full" :disabled="busy || !auth">
          {{ busy ? '登录中…' : '登录管理面板' }}</button
        ><button v-if="!auth" class="text-button" type="button" @click="initialize">
          重新连接
        </button>
      </form>
      <form v-else @submit.prevent="changePassword">
        <label
          >当前临时密码<input
            v-model="currentPassword"
            type="password"
            required
            autocomplete="current-password" /></label
        ><label
          >新密码<input
            v-model="newPassword"
            type="password"
            required
            minlength="8"
            maxlength="256"
            autocomplete="new-password" /></label
        ><label
          >确认新密码<input
            v-model="confirmPassword"
            type="password"
            required
            autocomplete="new-password"
        /></label>
        <p class="hint">至少 8 位，包含英文大写、小写、数字和符号。</p>
        <button class="primary full" :disabled="busy">修改密码并重新登录</button
        ><button class="text-button" type="button" @click="logout">退出登录</button>
      </form>
      <p class="login-note">会话有效期 8 小时 · 仅限站点管理员</p>
    </section>
  </main>
  <div v-else class="admin-shell">
    <aside class="sidebar">
      <strong class="sidebar-title">管理面板</strong>
      <nav aria-label="管理导航">
        <RouterLink to="/admin/devices" :class="{ selected: section === 'devices' }"
          >◫ <span>设备与状态</span></RouterLink
        ><RouterLink to="/admin/statistics" :class="{ selected: section === 'statistics' }"
          >▥ <span>访问统计</span></RouterLink
        ><RouterLink to="/admin/settings" :class="{ selected: section === 'settings' }"
          >⚙ <span>站点与安全</span></RouterLink
        >
      </nav>
      <div class="sidebar-bottom">
        <a href="/" target="_blank" rel="noopener">查看公开页面 ↗</a
        ><button class="text-button" :disabled="busy" @click="logout">退出登录</button>
      </div>
    </aside>
    <main class="admin-main">
      <header class="admin-header">
        <div>
          <h1>
            {{
              section === 'statistics'
                ? '访问统计'
                : section === 'settings'
                  ? '站点与安全'
                  : '设备与状态'
            }}
          </h1>
        </div>
      </header>
      <div v-if="error" class="notice error" role="alert">{{ error }}</div>
      <div v-if="success" class="notice" role="status">{{ success }}</div>
      <template v-if="section === 'devices'">
        <section class="panel status-panel">
          <div>
            <span class="eyebrow">当前总体状态</span>
            <h2>{{ snapshot?.presence.name }}</h2>
            <p class="muted">
              {{ snapshot?.presence.manual ? '手动覆盖中' : '根据公开设备自动判断' }}
            </p>
          </div>
          <form class="inline-form" @submit.prevent="saveStatus">
            <label
              >状态<select v-model="manual">
                <option value="">自动判断</option>
                <option v-for="(label, key) in settings?.labels" :key="key" :value="key">
                  {{ label.name }}
                </option>
              </select></label
            ><label v-if="manual"
              >持续时间<select v-model="duration">
                <option value="3600">1 小时</option>
                <option value="14400">4 小时</option>
                <option value="86400">24 小时</option>
                <option value="forever">直到手动解除</option>
              </select></label
            ><button class="secondary" :disabled="busy">应用</button>
          </form>
        </section>
        <div class="section-heading">
          <h2>
            我的设备 <span class="count">{{ snapshot?.devices.length || 0 }}</span>
          </h2>
          <div class="device-actions">
            <label class="check smtc-switch"
              ><input
                type="checkbox"
                :checked="!!snapshot?.smtc"
                :disabled="busy || !snapshot"
                @click.prevent="changeSmtc(!snapshot?.smtc)"
              />启用SMTC</label
            >
            <button class="primary" @click="edit = null">＋ 添加设备</button>
          </div>
        </div>
        <div v-if="snapshot" class="managed-devices">
          <MusicCard v-if="snapshot.smtc" :music="snapshot.smtc">
            <button class="text-button small" :disabled="busy" @click="changeSmtc(true, true)">
              重置 SMTC Key
            </button>
          </MusicCard>
          <div v-if="!snapshot.devices.length && !snapshot.smtc" class="panel empty-state">
            <h3>暂无设备</h3>
            <p>创建设备、保存专属 Key，然后在客户端填入站点地址。</p>
          </div>

          <article v-for="device in snapshot.devices" :key="device.id" class="panel managed-device">
            <div class="device-icon"><DeviceIcon :kind="device.kind" /></div>
            <div class="managed-info">
              <h3>
                {{ device.name }} <span v-if="device.config?.hidden" class="badge">已隐藏</span>
              </h3>
              <p class="muted small">
                {{ systemNames[device.os] }} · {{ kindNames[device.kind] }} ·
                {{ onlineNames[device.online] }}
              </p>
              <template v-if="device.online === 'offline'">
                <p
                  v-if="(device.hints ?? snapshot.site.display).offline"
                  class="activity-text small"
                >
                  {{ (device.hints ?? snapshot.site.display).offline }}
                </p>
              </template>
              <p v-else class="activity-text small">
                {{
                  device.state.status ||
                  (device.state.using === true ? '正在使用' : '暂无活动描述')
                }}<span v-if="powerText(device)"> · {{ powerText(device) }}</span>
              </p>
              <DeviceReadings :device="device" />
              <p v-if="device.last_updated" class="hint">
                上报于 {{ new Date(device.last_updated * 1000).toLocaleString() }}
              </p>
            </div>
            <div class="device-actions">
              <button class="secondary small" @click="edit = device">编辑设备</button
              ><button class="text-button small" :disabled="busy" @click="rotateKey(device)">
                重置 Key</button
              ><button
                class="text-button danger small"
                :disabled="busy"
                @click="removeDevice(device)"
              >
                删除
              </button>
            </div>
          </article>
        </div>
      </template>
      <template v-else-if="section === 'statistics'">
        <p class="muted">页面和 API 请求次数 · {{ statistics?.timezone }} · 不代表独立访客人数</p>
        <div class="statistics-grid">
          <article
            v-for="(name, key) in {
              day: '今日',
              week: '本周',
              month: '本月',
              year: '今年',
              total: '累计',
            }"
            :key="key"
            class="panel stat"
          >
            <span class="muted">{{ name }}</span
            ><strong>{{ statistics?.totals[key]?.toLocaleString() ?? '—' }}</strong>
          </article>
        </div>
        <section class="panel">
          <div class="section-heading">
            <h2>请求明细</h2>
            <div class="inline-form">
              <select v-model="statCategory" aria-label="统计类别">
                <option value="">全部类别</option>
                <option
                  v-for="(name, key) in {
                    page: '页面',
                    public_api: '公开 API',
                    admin_api: '管理 API',
                    report: '设备上报',
                    sse: '实时连接',
                    unmatched: '未匹配路径',
                  }"
                  :key="key"
                  :value="key"
                >
                  {{ name }}
                </option></select
              ><button class="secondary" :disabled="busy" @click="guarded(loadStatistics)">
                刷新
              </button>
            </div>
          </div>
          <div class="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>路由</th>
                  <th>方法</th>
                  <th>响应</th>
                  <th>次数</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, index) in statistics?.routes" :key="index">
                  <td>
                    <code>{{ item.route }}</code>
                  </td>
                  <td>{{ item.method }}</td>
                  <td>{{ item.status || '中断' }}</td>
                  <td>{{ item.count.toLocaleString() }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="hint">计数约每 5 秒保存。异常退出可能丢失尚未保存的计数。</p>
        </section>
      </template>
      <template v-else-if="settings">
        <form ref="settingsForm" @submit.prevent="flushSettings" @change="flushSettings">
          <p v-if="saveMessage" class="hint" role="status">{{ saveMessage }}</p>
          <div v-if="saveError" class="notice error" role="alert">
            尚未保存。{{ saveError }}
            <button type="button" class="text-button" @click="flushSettings">重试</button>
          </div>
          <section class="panel settings-panel">
            <h2>公开页面</h2>
            <label>站点标题<input v-model="settings.title" required maxlength="80" /></label
            ><label>简介<textarea v-model="settings.description" rows="2" maxlength="300" /></label>
            <label
              >无设备提示<input v-model.trim="settings.display.empty_devices" maxlength="200"
            /></label>
            <p class="hint">留空时，首页在没有公开设备时隐藏设备区。</p>
            <label
              >背景图片地址<input
                v-model.trim="settings.display.background_url"
                type="url"
                placeholder="https://"
            /></label>
            <p class="hint">填写图片直链或随机图接口，留空使用纯色背景。</p>
            <label
              >卡片不透明度（%）<input
                type="number"
                v-model.number="settings.display.card_opacity"
                required
                min="0"
                max="100"
            /></label>
            <div class="form-grid">
              <label
                >配色<select v-model="settings.theme">
                  <option value="system">跟随系统</option>
                  <option value="light">浅色</option>
                  <option value="dark">深色</option>
                </select></label
              ><label
                >统计时区<input v-model="settings.timezone" required placeholder="Asia/Shanghai"
              /></label>
            </div>
            <label class="check"
              ><input
                v-model="settings.hide_all"
                type="checkbox"
              />隐藏全部设备（仍接收上报）</label
            ><label class="check"
              ><input v-model="settings.using_first" type="checkbox" />正在使用的设备优先展示</label
            >
          </section>

          <section class="panel settings-panel">
            <h2>首页页脚</h2>
            <label
              >页脚文字<textarea v-model.trim="settings.display.footer" maxlength="300" rows="2" />
            </label>
            <div class="form-grid">
              <label
                >链接文字<input
                  v-model.trim="settings.display.link_text"
                  maxlength="80"
                  :required="!!settings.display.link_url"
              /></label>
              <label
                >链接地址<input
                  v-model.trim="settings.display.link_url"
                  type="url"
                  placeholder="https://"
                  :required="!!settings.display.link_text"
              /></label>
            </div>
            <div class="form-grid">
              <label
                >ICP备案号<input
                  v-model.trim="settings.display.icp_filing"
                  maxlength="80"
                  placeholder="留空则隐藏"
              /></label>
              <label
                >公安备案号<input
                  v-model.trim="settings.display.police_filing"
                  maxlength="80"
                  pattern="[^0-9]*[0-9]{14}\s*号?"
                  title="填写含 14 位数字的公安备案号"
                  placeholder="省简称 + 公网安备 + 14 位数字 + 号"
              /></label>
            </div>
          </section>
          <section class="panel settings-panel">
            <h2>状态名称、说明与颜色</h2>
            <div v-for="(label, key) in settings.labels" :key="key" class="label-editor">
              <span class="hint">{{ key }}</span
              ><input
                v-model="label.name"
                :aria-label="`${key}名称`"
                required
                maxlength="40"
              /><input v-model="label.color" type="color" :aria-label="`${key}颜色`" /><input
                v-model="label.description"
                :aria-label="`${key}说明`"
                placeholder="状态说明"
                maxlength="200"
              />
            </div>
            <div class="inline-form">
              <input
                v-model="newLabelId"
                aria-label="新状态标识"
                maxlength="32"
                placeholder="状态标识，例如 reading"
              /><input
                v-model.trim="newLabelName"
                aria-label="新状态名称"
                placeholder="显示名称，例如 看书中"
                maxlength="40"
              /><button
                type="button"
                class="secondary"
                :disabled="!newLabelId.trim() || !newLabelName.trim()"
                @click="addLabel"
              >
                添加状态
              </button>
            </div>
          </section>
          <section class="panel settings-panel">
            <h2>访问统计范围</h2>
            <div class="switches">
              <label
                v-for="(name, key) in {
                  page: '页面',
                  public_api: '公开 API',
                  admin_api: '管理 API',
                  report: '设备上报',
                  sse: '实时连接',
                  unmatched: '未匹配路径',
                }"
                :key="key"
                class="check"
                ><input v-model="settings.stats_categories" type="checkbox" :value="key" />{{
                  name
                }}</label
              >
            </div>
            <p class="hint">范围调整对未来请求生效，最多约 5 秒同步到入口；既有计数保留。</p>
          </section>
          <section class="panel settings-panel">
            <h2>访问入口 <span class="badge">重启后生效</span></h2>
            <p class="hint">
              当前监听 {{ runtime.host }}:{{ runtime.port }}。命令行显式参数会覆盖保存值。
            </p>
            <div class="form-grid">
              <label>监听地址<input v-model="settings.host" required /></label
              ><label
                >端口<input
                  v-model.number="settings.port"
                  type="number"
                  min="1"
                  max="65535"
                  required
              /></label>
            </div>
            <label
              >HTTPS 证书文件路径<input
                v-model="settings.ssl_certfile"
                placeholder="未填写时使用 HTTP" /></label
            ><label>HTTPS 私钥文件路径<input v-model="settings.ssl_keyfile" /></label
            ><label
              >可信网关 IP / CIDR<input v-model="proxyList" placeholder="逗号分隔；直接访问时留空"
            /></label>
            <p class="hint">证书由部署者签发和续期。默认只信任直接连接，不信任外部提交的代理头。</p>
          </section>
        </form>
        <section class="panel settings-panel">
          <h2>API Token</h2>
          <p class="hint">
            用于脚本读写站点设置、切换当前状态。在请求头中传入
            <code>Authorization: Bearer &lt;token&gt;</code>。Token 在撤销前有效。
          </p>
          <form class="inline-form" @submit.prevent="createToken">
            <label
              >Token 名称<input
                v-model.trim="tokenName"
                maxlength="80"
                required
                placeholder="例如：站点同步脚本"
            /></label>
            <button class="secondary" :disabled="busy || !tokenName">创建 API Token</button>
          </form>
          <ul v-if="tokens.length" class="token-list">
            <li v-for="item in tokens" :key="item.id">
              <div>
                <strong>{{ item.name }}</strong>
                <p class="small muted">
                  创建于 {{ new Date(item.created_at * 1000).toLocaleString() }}
                </p>
              </div>
              <button
                type="button"
                class="text-button danger"
                :disabled="busy"
                :aria-label="`撤销 ${item.name}`"
                @click="revokeToken(item)"
              >
                撤销
              </button>
            </li>
          </ul>
        </section>
        <section class="panel settings-panel security-panel">
          <h2>修改管理员密码</h2>
          <form @submit.prevent="changePassword">
            <label
              >当前密码<input
                v-model="currentPassword"
                type="password"
                required
                autocomplete="current-password"
            /></label>
            <div class="form-grid">
              <label
                >新密码<input
                  v-model="newPassword"
                  type="password"
                  required
                  minlength="8"
                  maxlength="256"
                  autocomplete="new-password" /></label
              ><label
                >确认新密码<input
                  v-model="confirmPassword"
                  type="password"
                  required
                  autocomplete="new-password"
              /></label>
            </div>
            <p class="hint">至少 8 位，包含英文大小写、数字和符号；修改后所有管理会话失效。</p>
            <button class="secondary" :disabled="busy">修改密码</button>
          </form>
        </section>
      </template>
    </main>
    <DeviceEditor
      v-if="edit !== undefined && snapshot"
      :defaults="snapshot.site.display"
      :initial="edit?.config"
      :busy="busy"
      :error="error"
      @cancel="edit = undefined"
      @save="saveDevice"
    />
    <div v-if="freshKey" class="modal-backdrop">
      <section class="modal key-modal" role="dialog" aria-modal="true" aria-labelledby="key-title">
        <h2 id="key-title">
          {{ keyOwner === 'API' ? '保存 API Token' : `保存${keyOwner}的专属 Key` }}
        </h2>
        <p v-if="keyOwner === 'API'" class="muted">
          完整 Token 仅在创建时显示，请复制后保存到脚本的密钥配置中。
        </p>
        <p v-else class="muted">仅在创建或重置时显示。将站点地址与此 Key 填入客户端。</p>
        <div v-if="error" class="notice error" role="alert">{{ error }}</div>
        <code class="secret-key">{{ freshKey }}</code>
        <div class="form-actions">
          <button class="secondary" @click="copyKey">复制 {{ credentialLabel }}</button
          ><button class="primary" @click="freshKey = ''">已保存，关闭</button>
        </div>
      </section>
    </div>
  </div>
</template>
