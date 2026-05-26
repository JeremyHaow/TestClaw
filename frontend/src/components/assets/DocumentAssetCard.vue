<script setup lang="ts">
import { computed } from 'vue'
import { Database, Eye, Pencil, Play, Sparkles, Trash2 } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  item: Record<string, any>
  selected?: boolean
}>(), {
  selected: false,
})

defineEmits<{
  (event: 'view'): void
  (event: 'run'): void
  (event: 'plan'): void
  (event: 'edit'): void
  (event: 'delete'): void
}>()

const endpoints = computed(() => (
  Array.isArray(props.item.parsed_endpoints) ? props.item.parsed_endpoints : []
))
const endpointCount = computed(() => endpoints.value.length)
const authCount = computed(() => endpoints.value.filter((endpoint: any) => endpoint?.auth_required).length)
const title = computed(() => props.item.name || `Document-${props.item.format || 'openapi'}`)
const sourceLabel = computed(() => props.item.source_url || '已保存原文')
const statusLabel = computed(() => endpointCount.value ? 'Ready' : '待解析')
const statusClass = computed(() => endpointCount.value
  ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
  : 'border-amber-200 bg-amber-50 text-amber-700')
const cardClass = computed(() => props.selected
  ? 'border-blue-300 bg-blue-50/50 shadow-[0_12px_30px_rgba(37,99,235,0.10)]'
  : 'border-gray-200 bg-white shadow-sm hover:border-blue-200 hover:shadow-[0_12px_30px_rgba(15,23,42,0.08)]')
</script>

<template>
  <article
    data-testid="document-asset-card"
    class="rounded-lg border p-4 transition-all"
    :class="cardClass"
  >
    <div class="flex items-start gap-3">
      <div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
        <Database :size="18" />
      </div>
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="truncate text-sm font-semibold text-gray-950">{{ title }}</h3>
          <span class="rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase" :class="statusClass">
            {{ statusLabel }}
          </span>
        </div>
        <p class="mt-1 truncate font-mono text-[11px] text-gray-500">{{ sourceLabel }}</p>
      </div>
    </div>

    <div class="mt-4 grid grid-cols-3 gap-2">
      <div class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
        <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">格式</div>
        <div class="mt-1 truncate text-xs font-semibold uppercase text-gray-800">{{ item.format || 'openapi' }}</div>
      </div>
      <div class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
        <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">端点</div>
        <div class="mt-1 text-xs font-semibold text-gray-800">{{ endpointCount }}</div>
      </div>
      <div class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
        <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">鉴权</div>
        <div class="mt-1 text-xs font-semibold text-gray-800">{{ authCount }}</div>
      </div>
    </div>

    <div class="mt-4 flex flex-wrap items-center gap-2">
      <button
        type="button"
        class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-700 transition-all hover:border-gray-300 hover:bg-gray-50"
        @click="$emit('view')"
      >
        <Eye :size="13" /> 查看接口
      </button>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 rounded-lg bg-gray-950 px-3 py-1.5 text-xs font-bold text-white transition-all hover:bg-gray-800"
        @click="$emit('run')"
      >
        <Play :size="13" /> 运行测试
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
          title="更新文档"
          aria-label="更新文档"
          @click="$emit('edit')"
        >
          <Pencil :size="14" />
        </button>
        <button
          type="button"
          class="rounded-lg p-1.5 text-gray-400 transition-all hover:bg-red-50 hover:text-red-600"
          title="删除文档"
          aria-label="删除文档"
          @click="$emit('delete')"
        >
          <Trash2 :size="14" />
        </button>
      </div>
    </div>
  </article>
</template>
