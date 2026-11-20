export const onlineNames: Record<string, string> = {
  online: '在线',
  offline: '离线',
  unknown: '等待上报',
  disabled: '已停用',
}
export type Kind = 'phone' | 'tablet' | 'laptop' | 'desktop' | 'server'
export type System = 'windows' | 'android' | 'linux'
export interface CustomField {
  key: string
  name: string
  type: 'text' | 'number' | 'boolean'
  role?: 'external_power' | null
}
export interface DeviceHints {
  in_use: string
  not_using: string
  offline: string
  device_slice: number
}
export interface DeviceConfig {
  name: string
  kind: Kind
  os: System
  enabled: boolean
  hidden: boolean
  order: number
  timeout: number
  participates: boolean
  custom_fields: CustomField[]
  hints: DeviceHints | null
  enabled_fields: string[]
  receive_fields: string[]
  public_fields: string[]
}
export interface Device {
  id: string
  name: string
  kind: Kind
  os: System
  online: string
  order: number
  hints: DeviceHints | null
  custom_fields: CustomField[]
  state: {
    [key: string]: string | number | boolean | null | undefined
    using?: boolean | null
    status?: string | null
    battery_percent?: number | null
    charging?: boolean | null
  }
  config?: DeviceConfig
  last_updated?: number | null
}
export interface DisplaySettings {
  empty_devices: string
  background_url: string
  card_opacity: number
  footer: string
  link_text: string
  link_url: string
  icp_filing: string
  police_filing: string
}
export interface MusicState {
  playback: 'unknown' | 'idle' | 'paused' | 'playing'
  title?: string
  artist?: string
  album?: string
}
export interface Snapshot {
  smtc: MusicState | null
  version: string
  site: Pick<Settings, 'title' | 'description' | 'theme'> & {
    display: DisplaySettings & DeviceHints
    filings: { kind: 'icp' | 'police'; text: string; url: string }[]
  }
  presence: { code: string; name: string; description: string; color: string; manual: boolean }
  devices: Device[]
  manual?: { status: string; expires: number | null } | null
}
export interface Auth {
  authenticated: boolean
  must_change: boolean
  csrf: string
  expires: number
}
export interface Label {
  name: string
  description: string
  color: string
}
export interface Settings {
  title: string
  description: string
  theme: string
  display: DisplaySettings
  hide_all: boolean
  using_first: boolean
  timezone: string
  stats_categories: string[]
  host: string
  port: number
  ssl_certfile: string | null
  ssl_keyfile: string | null
  trusted_proxies: string[]
  labels: Record<string, Label>
}
export interface SettingsToken {
  id: string
  name: string
  created_at: number
}

export interface Statistics {
  timezone: string
  totals: Record<string, number>
  routes: { category: string; route: string; method: string; status: number; count: number }[]
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}
export async function api<T>(path: string, method = 'GET', body?: unknown, csrf = ''): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    const detail = error.errors
      ?.map((e: { loc: string[]; msg: string }) => `${e.loc.join('.')}: ${e.msg}`)
      .join('；')
    throw new ApiError(response.status, detail || error.detail || `请求失败 (${response.status})`)
  }
  return response.status === 204 ? (undefined as T) : response.json()
}
export const kindNames: Record<Kind, string> = {
  phone: '手机',
  tablet: '平板',
  laptop: '笔记本',
  desktop: '主机',
  server: '服务器',
}
export const systemNames: Record<System, string> = {
  windows: 'Windows',
  android: 'Android',
  linux: 'Linux',
}
export const metrics = [
  { key: 'cpu_percent', name: 'CPU 使用率' },
  { key: 'gpu_percent', name: 'GPU 使用率' },
  { key: 'memory_percent', name: '内存使用率' },
]
export const fields = [
  { key: 'using', name: '使用状态' },
  { key: 'status', name: '活动描述' },
  { key: 'battery_percent', name: '电池电量' },
  { key: 'charging', name: '充电状态' },
  ...metrics,
]

export function deviceFields(custom: CustomField[]) {
  return [...fields.filter((f) => !custom.some((c) => c.key === f.key)), ...custom]
}

export function powerText(device: Device) {
  const { battery_percent: percent, charging } = device.state
  const field = device.custom_fields.find((f) => f.role === 'external_power')
  const ac = field && device.state[field.key]
  const parts: string[] = []
  if (percent != null) parts.push(`电量 ${percent}%`)
  if (ac === false) parts.push('电池供电')
  else {
    if (ac === true) parts.push('已接电')
    if (charging === true) parts.push('充电中')
    else if (ac === true && percent === 100) parts.push('已充满')
    else if (charging === false) parts.push('未充电')
  }
  return parts.join(' · ')
}
