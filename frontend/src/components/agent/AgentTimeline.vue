<script setup lang="ts">
import { Clock } from 'lucide-vue-next'

withDefaults(
  defineProps<{
    items: Record<string, any>[]
    active?: boolean
  }>(),
  {
    items: () => [],
    active: false,
  },
)

function markerClass(status: string) {
  const value = String(status || '').toLowerCase()
  if (['failed', 'error', 'blocked', 'needs_human'].includes(value)) return 'bg-red-500'
  if (['done', 'success', 'sufficient', 'passed'].includes(value)) return 'bg-emerald-500'
  if (['needs_replan', 'needs_retry', 'insufficient'].includes(value)) return 'bg-blue-500'
  if (value === 'cancelled') return 'bg-gray-400'
  return 'bg-blue-500'
}

function eventTitle(item: Record<string, any>) {
  return item.node || item.stage || item.event || '执行事件'
}

function eventDetail(item: Record<string, any>) {
  return item.detail || item.message || ''
}
</script>

<template>
  <section class="mt-5">
    <div class="mb-3 flex items-center justify-between">
      <h3 class="flex items-center gap-1.5 text-xs font-bold uppercase tracking-widest text-gray-400">
        <Clock :size="14" />
        Agent Timeline
        <span class="font-sans text-gray-500">最近活动</span>
      </h3>
      <span v-if="items.length" class="text-[10px] font-bold text-gray-400">{{ items.length }} 条</span>
    </div>
    <div v-if="items.length" class="space-y-2">
      <div
        v-for="(item, idx) in items"
        :key="`${item.source}-${item.timestamp || item.node || idx}`"
        class="flex items-start gap-3 rounded-lg border border-gray-100 bg-white px-3 py-2 text-xs"
      >
        <span class="mt-0.5 h-2 w-2 shrink-0 rounded-full" :class="markerClass(item.status)"></span>
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-bold text-gray-800">{{ eventTitle(item) }}</span>
            <span class="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-bold text-gray-500">{{ item.status || item.source }}</span>
          </div>
          <p v-if="eventDetail(item)" class="mt-0.5 text-gray-500">{{ eventDetail(item) }}</p>
        </div>
        <span v-if="item.timestamp" class="shrink-0 font-mono text-[10px] text-gray-400">{{ new Date(item.timestamp).toLocaleTimeString('zh-CN') }}</span>
      </div>
    </div>
    <div v-else class="rounded-lg border border-dashed border-gray-200 bg-gray-50 px-4 py-8 text-center text-sm text-gray-400">
      {{ active ? '等待第一条执行活动' : '等待执行活动' }}
    </div>
  </section>
</template>
