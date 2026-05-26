<script setup lang="ts">
import { Camera, FileText } from 'lucide-vue-next'

withDefaults(
  defineProps<{
    evidenceCount?: number
    findingCount?: number
    riskLabel?: string
  }>(),
  {
    evidenceCount: 0,
    findingCount: 0,
    riskLabel: '等待结果',
  },
)
</script>

<template>
  <section class="space-y-4">
    <div class="flex flex-col gap-3 rounded-lg border border-gray-200 bg-white px-4 py-3 shadow-sm sm:flex-row sm:items-center sm:justify-between">
      <div>
        <div class="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-gray-400">
          <Camera :size="14" />
          证据 / Findings
        </div>
        <p class="mt-1 text-sm text-gray-600">汇总本次运行的证据、阻断发现和发布风险。</p>
      </div>
      <div class="grid grid-cols-3 gap-2 text-center">
        <div class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
          <div class="text-[10px] font-bold text-gray-400">证据</div>
          <div class="mt-0.5 text-sm font-bold text-gray-900">{{ evidenceCount }}</div>
        </div>
        <div class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
          <div class="text-[10px] font-bold text-gray-400">发现</div>
          <div class="mt-0.5 text-sm font-bold text-gray-900">{{ findingCount }}</div>
        </div>
        <div class="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
          <div class="flex items-center justify-center gap-1 text-[10px] font-bold text-gray-400">
            <FileText :size="12" />
            风险
          </div>
          <div class="mt-0.5 truncate text-sm font-bold text-gray-900">{{ riskLabel }}</div>
        </div>
      </div>
    </div>
    <slot />
  </section>
</template>
