<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status: string }>()

const config = computed(() => {
  const status = String(props.status || '').toLowerCase()
  const map: Record<string, { bg: string; text: string; border: string; label: string }> = {
    succeeded: { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-100', label: 'SUCCESS' },
    success: { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-100', label: 'SUCCESS' },
    done: { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-100', label: 'DONE' },
    failed: { bg: 'bg-red-50', text: 'text-red-700', border: 'border-red-100', label: 'FAILED' },
    bug_found: { bg: 'bg-red-50', text: 'text-red-700', border: 'border-red-100', label: 'BUG FOUND' },
    cancelled: { bg: 'bg-slate-50', text: 'text-slate-600', border: 'border-slate-200', label: 'CANCELLED' },
    running: { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-100', label: 'RUNNING' },
    queued: { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-100', label: 'QUEUED' },
    pending: { bg: 'bg-gray-50', text: 'text-gray-600', border: 'border-gray-200', label: 'PENDING' },
  }
  return map[status] || { bg: 'bg-gray-50', text: 'text-gray-600', border: 'border-gray-200', label: String(props.status || 'unknown').toUpperCase() }
})
</script>

<template>
  <span
    class="inline-flex items-center whitespace-nowrap rounded-md border px-2 py-0.5 text-[10px] font-bold"
    :class="[config.bg, config.text, config.border]"
  >
    {{ config.label }}
  </span>
</template>
