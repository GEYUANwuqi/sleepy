<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import {
  deviceFields,
  kindNames,
  systemNames,
  type DeviceConfig,
  type Kind,
  type DeviceHints,
  type CustomField,
} from './api'
import DeviceFieldPicker from './DeviceFieldPicker.vue'
import DeviceHintInputs from './DeviceHintInputs.vue'
const props = defineProps<{
  initial?: DeviceConfig
  defaults: DeviceHints
  busy: boolean
  error: string
}>()
const emit = defineEmits<{ save: [DeviceConfig]; cancel: [] }>()
const form = ref<DeviceConfig>(
  props.initial
    ? { hints: null, custom_fields: [], ...JSON.parse(JSON.stringify(props.initial)) }
    : {
        name: '',
        custom_fields: [],
        hints: null,
        kind: 'desktop',
        os: 'windows',
        enabled: true,
        hidden: false,
        order: 0,
        timeout: 180,
        participates: true,
        enabled_fields: ['using', 'status'],
        receive_fields: ['using', 'status'],
        public_fields: ['using'],
      },
)
const picking = ref(false)
const addButton = ref<HTMLButtonElement>()
const activeFields = computed(() =>
  deviceFields(form.value.custom_fields).filter((f) => form.value.enabled_fields.includes(f.key)),
)
function isCustom(field: { key: string; name: string }): field is CustomField {
  return 'type' in field
}
function closePicker() {
  picking.value = false
  nextTick(() => addButton.value?.focus())
}
function applyFields(keys: string[], custom: CustomField[]) {
  form.value.custom_fields = custom
  form.value.enabled_fields.push(...keys)
  closePicker()
}
function disableField(key: string) {
  form.value.enabled_fields = form.value.enabled_fields.filter((k) => k !== key)
  form.value.receive_fields = form.value.receive_fields.filter((k) => k !== key)
  cleanPublic()
}
function removeField(key: string) {
  disableField(key)
  form.value.custom_fields = form.value.custom_fields.filter((f) => f.key !== key)
}
function setPowerField(key: string, checked: boolean) {
  for (const f of form.value.custom_fields) {
    if (f.key === key && checked) f.role = 'external_power'
    else delete f.role
  }
}
function setHints(enabled: boolean) {
  const { in_use, not_using, offline, device_slice } = props.defaults
  form.value.hints = enabled ? { in_use, not_using, offline, device_slice } : null
}
function kindChanged() {
  if (props.initial) return
  form.value.participates = form.value.kind !== 'server'
  const portable = ['phone', 'tablet', 'laptop'].includes(form.value.kind)
  for (const key of ['battery_percent', 'charging']) {
    if (!portable) disableField(key)
    else if (!form.value.enabled_fields.includes(key)) {
      form.value.enabled_fields.push(key)
      form.value.receive_fields.push(key)
    }
  }
}
function cleanPublic() {
  form.value.public_fields = form.value.public_fields.filter((f) =>
    form.value.receive_fields.includes(f),
  )
}
</script>
<template>
  <div class="modal-backdrop" @click.self="!busy && emit('cancel')">
    <section
      class="modal"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="picking ? 'field-picker-title' : 'device-title'"
    >
      <DeviceFieldPicker v-if="picking" :device="form" @apply="applyFields" @cancel="closePicker" />
      <template v-else>
        <div class="modal-heading">
          <h2 id="device-title">{{ initial ? '编辑设备' : '添加设备' }}</h2>
          <button class="icon-button" aria-label="关闭" :disabled="busy" @click="emit('cancel')">
            ×
          </button>
        </div>
        <div v-if="error" class="notice error" role="alert">{{ error }}</div>
        <form @submit.prevent="emit('save', form)">
          <label
            >设备名称<input
              v-model="form.name"
              required
              maxlength="80"
              placeholder="例如：我的工作电脑"
              autofocus
          /></label>
          <div class="form-grid">
            <label
              >设备类别<select v-model="form.kind" @change="kindChanged">
                <option v-for="(name, key) in kindNames" :key="key" :value="key as Kind">
                  {{ name }}
                </option>
              </select></label
            ><label
              >操作系统<select v-model="form.os">
                <option v-for="(name, key) in systemNames" :key="key" :value="key">
                  {{ name }}
                </option>
              </select></label
            >
          </div>
          <div class="form-grid">
            <label
              >离线阈值（秒）<input
                v-model.number="form.timeout"
                type="number"
                min="10"
                max="86400"
                required /></label
            ><label
              >排序（小值靠前）<input
                v-model.number="form.order"
                type="number"
                min="-10000"
                max="10000"
                required
            /></label>
          </div>
          <div class="switches">
            <label class="check"><input v-model="form.enabled" type="checkbox" />启用设备上报</label
            ><label class="check"><input v-model="form.hidden" type="checkbox" />隐藏此设备</label
            ><label class="check"
              ><input v-model="form.participates" type="checkbox" />参与总体状态判断</label
            >
          </div>
          <fieldset>
            <legend>字段接收与公开范围</legend>
            <div class="field-cards">
              <article v-for="field in activeFields" :key="field.key" class="field-card">
                <div>
                  <template v-if="isCustom(field)">
                    <input
                      v-model.trim="field.name"
                      required
                      maxlength="40"
                      :aria-label="`${field.key} 显示名称`"
                    />
                    <select
                      v-model="field.type"
                      @change="delete field.role"
                      :aria-label="`${field.key} 字段类型`"
                    >
                      <option value="text">文本</option>
                      <option value="number">数值</option>
                      <option value="boolean">布尔值</option>
                    </select>
                    <label v-if="field.type === 'boolean'" class="check"
                      ><input
                        type="checkbox"
                        :checked="field.role === 'external_power'"
                        @change="
                          setPowerField(field.key, ($event.target as HTMLInputElement).checked)
                        "
                      />用于显示外接电源状态</label
                    >
                  </template>
                  <strong v-else>{{ field.name }}</strong>
                  <code class="field-key">{{ field.key }}</code>
                </div>
                <div class="field-permissions">
                  <label class="check"
                    ><input
                      v-model="form.receive_fields"
                      type="checkbox"
                      :value="field.key"
                      :aria-label="`接收${field.name}`"
                      @change="cleanPublic"
                    />接收</label
                  >
                  <label class="check"
                    ><input
                      v-model="form.public_fields"
                      type="checkbox"
                      :value="field.key"
                      :aria-label="`公开${field.name}`"
                      :disabled="!form.receive_fields.includes(field.key)"
                    />公开</label
                  >
                </div>
                <div class="field-actions">
                  <button
                    type="button"
                    class="text-button small"
                    :aria-label="`停用${field.name}`"
                    @click="disableField(field.key)"
                  >
                    停用
                  </button>
                  <button
                    v-if="isCustom(field)"
                    type="button"
                    class="text-button danger small"
                    :aria-label="`删除${field.name}`"
                    @click="removeField(field.key)"
                  >
                    删除字段
                  </button>
                </div>
              </article>
              <button
                ref="addButton"
                type="button"
                class="field-card add-field"
                @click="picking = true"
              >
                <span aria-hidden="true">+</span>添加字段
              </button>
            </div>
          </fieldset>
          <fieldset>
            <legend>设备提示</legend>
            <label class="check"
              ><input
                type="checkbox"
                :checked="!!form.hints"
                @change="setHints(($event.target as HTMLInputElement).checked)"
              />自定义设备提示</label
            >
            <DeviceHintInputs v-if="form.hints" :hints="form.hints" />
          </fieldset>
          <p v-if="initial" class="hint">
            修改类别或系统会清空当前动态。取消接收或修改字段类型会清除该字段的当前值。
          </p>
          <div class="form-actions">
            <button type="button" class="secondary" :disabled="busy" @click="emit('cancel')">
              取消</button
            ><button class="primary" :disabled="busy">{{ busy ? '保存中…' : '保存设备' }}</button>
          </div>
        </form>
      </template>
    </section>
  </div>
</template>
