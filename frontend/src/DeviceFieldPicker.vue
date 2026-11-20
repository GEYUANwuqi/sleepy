<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { deviceFields, fields, type DeviceConfig, type CustomField } from './api'

const props = defineProps<{ device: DeviceConfig }>()
const emit = defineEmits<{ apply: [keys: string[], custom: CustomField[]]; cancel: [] }>()
const customFields = ref([...props.device.custom_fields])
const available = computed(() =>
  deviceFields(customFields.value).filter((f) => !props.device.enabled_fields.includes(f.key)),
)
const selected = ref<string[]>([])
const key = ref('')
const name = ref('')
const type = ref<CustomField['type']>('text')
const error = ref('')
const heading = ref<HTMLElement>()
onMounted(() => heading.value?.focus())

function createField() {
  if (!/^[a-z][a-z0-9_]{0,31}$/.test(key.value) || !name.value) {
    error.value = '请填写显示名称和标识。标识以小写字母开头，最多 32 位，可包含数字和下划线。'
    return
  }
  if ([...fields, ...customFields.value].some((f) => f.key === key.value)) {
    error.value = '该设备已有这个字段标识。'
    return
  }
  customFields.value.push({
    key: key.value,
    name: name.value,
    type: type.value,
  })
  key.value = ''
  name.value = ''
  type.value = 'text'
  error.value = ''
}
function deleteField(key: string) {
  customFields.value = customFields.value.filter((f) => f.key !== key)
  selected.value = selected.value.filter((k) => k !== key)
}
</script>
<template>
  <div>
    <div class="modal-heading">
      <h2 id="field-picker-title" ref="heading" tabindex="-1">添加设备字段</h2>
      <button type="button" class="icon-button" aria-label="关闭字段选择" @click="emit('cancel')">
        ×
      </button>
    </div>
    <fieldset v-if="available.length">
      <legend>可启用字段</legend>
      <div class="field-choices">
        <div v-for="field in available" :key="field.key" class="field-choice">
          <label class="check">
            <input
              type="checkbox"
              v-model="selected"
              :value="field.key"
              :aria-label="`启用${field.name}`"
            />
            <span
              >{{ field.name }}<code class="field-key">{{ field.key }}</code></span
            >
          </label>
          <button
            v-if="'type' in field"
            type="button"
            class="text-button small danger"
            :aria-label="`删除${field.name}`"
            @click="deleteField(field.key)"
          >
            删除
          </button>
        </div>
      </div>
    </fieldset>
    <form class="custom-field-form" @submit.prevent="createField">
      <fieldset>
        <legend>新建自定义字段</legend>
        <div class="form-grid">
          <label
            >字段标识<input v-model.trim="key" maxlength="32" placeholder="temperature"
          /></label>
          <label
            >显示名称<input v-model.trim="name" maxlength="40" placeholder="温度（℃）"
          /></label>
        </div>
        <label
          >字段类型<select v-model="type">
            <option value="text">文本</option>
            <option value="number">数值</option>
            <option value="boolean">布尔值</option>
          </select></label
        >
        <button type="submit" class="secondary">创建自定义字段</button>
        <p v-if="error" class="notice error" role="alert">{{ error }}</p>
      </fieldset>
    </form>
    <div class="form-actions">
      <button type="button" class="secondary" @click="emit('cancel')">取消</button>
      <button
        type="button"
        class="primary"
        :disabled="!!key || !!name"
        @click="emit('apply', selected, customFields)"
      >
        完成
      </button>
    </div>
  </div>
</template>
