<script setup lang="ts">
import { computed } from 'vue'
import { Copy, Globe, Pencil, Play, Shield, Sparkles, Trash2 } from 'lucide-vue-next'

const props = defineProps<{
  item: Record<string, any>
}>()

defineEmits<{
  (event: 'run'): void
  (event: 'plan'): void
  (event: 'edit'): void
  (event: 'copy'): void
  (event: 'delete'): void
}>()

function maskedValue(value: any) {
  const text = String(value ?? '')
  if (!text) return ''
  if (text.includes('*')) return text
  if (text.length <= 4) return '*'.repeat(text.length)
  return `${'*'.repeat(Math.min(text.length - 4, 12))}${text.slice(-4)}`
}

const variableEntries = computed(() => Object.entries(props.item.variables || {}))
const runnable = computed(() => Boolean(props.item.base_url))
const statusClass = computed(() => runnable.value
  ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
  : 'border-amber-200 bg-amber-50 text-amber-700')
const policyLabel = computed(() => props.item.is_production ? '生产只读' : '默认只读')
const healthItems = computed(() => [
  {
    label: 'Base URL',
    ok: runnable.value,
    text: runnable.value ? '已配置' : '待补充',
  },
  {
    label: '变量',
    ok: true,
    text: variableEntries.value.length ? `${variableEntries.value.length} 个键已脱敏` : '未配置',
  },
  {
    label: '安全策略',
    ok: true,
    text: policyLabel.value,
  },
])
</script>

<template>
  <article
    data-testid="environment-asset-card"
    class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm transition-all hover:border-blue-200 hover:shadow-[0_12px_30px_rgba(15,23,42,0.08)]"
  >
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div class="flex min-w-0 items-start gap-3">
        <div
          class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg"
          :class="item.is_production ? 'bg-amber-50 text-amber-600' : 'bg-blue-50 text-blue-600'"
        >
          <Globe :size="18" />
        </div>
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <h3 class="truncate text-sm font-semibold text-gray-950">{{ item.name }}</h3>
            <span class="rounded-full border px-2 py-0.5 text-[10px] font-bold" :class="statusClass">
              {{ runnable ? '可运行' : '缺 Base URL' }}
            </span>
            <span
              v-if="item.is_production"
              class="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-700"
            >
              <Shield :size="11" /> PROD
            </span>
          </div>
          <p class="mt-1 truncate font-mono text-[11px] text-gray-500">{{ item.base_url || '未配置 Base URL' }}</p>
        </div>
      </div>
    </div>

    <div class="mt-4 grid gap-2 sm:grid-cols-3">
      <div
        v-for="health in healthItems"
        :key="health.label"
        class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2"
      >
        <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">{{ health.label }}</div>
        <div class="mt-1 text-xs font-semibold" :class="health.ok ? 'text-emerald-700' : 'text-amber-700'">
          {{ health.ok ? 'OK' : 'TODO' }} · {{ health.text }}
        </div>
      </div>
    </div>

    <div v-if="variableEntries.length" class="mt-4 max-h-28 overflow-y-auto rounded-lg border border-gray-100 bg-gray-50 p-3">
      <div
        v-for="[key, value] in variableEntries"
        :key="key"
        class="grid grid-cols-[minmax(90px,160px)_minmax(0,1fr)] gap-2 py-0.5 text-xs font-mono"
      >
        <span class="truncate font-bold text-gray-400">{{ key }}</span>
        <span class="truncate text-gray-600">{{ maskedValue(value) }}</span>
      </div>
    </div>
    <div v-else class="mt-4 rounded-lg border border-dashed border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-400">
      未配置变量
    </div>

    <div class="mt-4 flex flex-wrap items-center gap-2">
      <button
        v-if="runnable"
        type="button"
        class="inline-flex items-center gap-1.5 rounded-lg bg-gray-950 px-3 py-1.5 text-xs font-bold text-white transition-all hover:bg-gray-800"
        @click="$emit('run')"
      >
        <Play :size="13" /> 用于运行
      </button>
      <button
        v-else
        type="button"
        class="inline-flex items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-bold text-amber-700 transition-all hover:bg-amber-100"
        @click="$emit('edit')"
      >
        <Pencil :size="13" /> 补 Base URL
      </button>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-bold text-blue-700 transition-all hover:bg-blue-100"
        @click="$emit('plan')"
      >
        <Sparkles :size="13" /> 用于新计划
      </button>
      <div class="ml-auto flex items-center gap-1">
        <button
          type="button"
          class="rounded-lg p-1.5 text-gray-400 transition-all hover:bg-blue-50 hover:text-blue-600"
          title="编辑"
          aria-label="编辑环境"
          @click="$emit('edit')"
        >
          <Pencil :size="14" />
        </button>
        <button
          type="button"
          class="rounded-lg p-1.5 text-gray-400 transition-all hover:bg-emerald-50 hover:text-emerald-600"
          title="复制结构"
          aria-label="复制环境结构"
          @click="$emit('copy')"
        >
          <Copy :size="14" />
        </button>
        <button
          type="button"
          class="rounded-lg p-1.5 text-gray-400 transition-all hover:bg-red-50 hover:text-red-600"
          title="删除"
          aria-label="删除环境"
          @click="$emit('delete')"
        >
          <Trash2 :size="14" />
        </button>
      </div>
    </div>
  </article>
</template>
