<script setup lang="ts">
import type { PropType } from 'vue'

type ApiPolicy = {
  value: string
  label: string
  desc: string
}

defineProps({
  modelValue: {
    type: String,
    required: true,
  },
  isApiMode: {
    type: Boolean,
    required: true,
  },
  apiPolicies: {
    type: Array as PropType<ApiPolicy[]>,
    required: true,
  },
})

const emit = defineEmits<{
  (event: 'update:modelValue', policy: string): void
}>()
</script>

<template>
  <div v-if="isApiMode">
    <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">API 执行策略</label>
    <div class="grid gap-3 md:grid-cols-3">
      <button
        v-for="policy in apiPolicies"
        :key="policy.value"
        class="min-w-0 rounded-lg border p-3 text-left transition-all"
        :class="modelValue === policy.value ? 'border-emerald-500 bg-emerald-50 text-emerald-800' : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300'"
        @click="emit('update:modelValue', policy.value)"
      >
        <div class="text-sm font-bold">{{ policy.label }}</div>
        <p class="mt-1 text-xs leading-5 text-gray-500">{{ policy.desc }}</p>
      </button>
    </div>
  </div>
  <div v-else class="rounded-lg border border-violet-100 bg-violet-50 px-4 py-3 text-sm text-violet-800">
    UI 巡检会使用浏览器执行路径、截图证据和登录前置说明；API 写入策略不会应用到本次运行。
  </div>
</template>
