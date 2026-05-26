<script setup lang="ts">
import { computed } from 'vue'
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
} from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    status?: string
    progressPercent?: number
    currentStep?: string
    blockingText?: string
    generatedCases?: number
    executedCount?: number
    passedCount?: number
    failedCount?: number
    skippedCount?: number
    evidenceCount?: number
  }>(),
  {
    status: '',
    progressPercent: 0,
    currentStep: '',
    blockingText: '',
    generatedCases: 0,
    executedCount: 0,
    passedCount: 0,
    failedCount: 0,
    skippedCount: 0,
    evidenceCount: 0,
  },
)

const normalizedBlocker = computed(() => props.blockingText || '暂无阻塞')
</script>

<template>
  <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
    <div class="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
      <div class="min-w-0">
        <div class="flex items-center gap-2">
          <BarChart3 :size="17" class="text-blue-600" />
          <h3 class="text-sm font-bold text-gray-900">运行摘要</h3>
          <span class="rounded bg-gray-100 px-2 py-0.5 text-[10px] font-bold uppercase text-gray-500">{{ status || 'unknown' }}</span>
        </div>
        <div class="mt-2 text-xs leading-5 text-gray-500">
          当前动作：{{ currentStep || '初始化' }}
        </div>
      </div>
      <div class="min-w-[220px]">
        <div class="mb-2 flex items-center justify-between text-[10px] font-bold uppercase tracking-widest text-gray-400">
          <span>进度</span>
          <span>{{ progressPercent }}%</span>
        </div>
        <div class="h-2 overflow-hidden rounded-full border border-gray-200 bg-gray-50">
          <div class="h-full rounded-full bg-blue-600 transition-all duration-500" :style="{ width: `${progressPercent}%` }"></div>
        </div>
      </div>
    </div>

    <div v-if="$slots.badges" class="mt-3 flex flex-wrap gap-2">
      <slot name="badges" />
    </div>

    <div class="mt-4 grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
      <div class="rounded-lg border border-gray-100 bg-gray-50 p-3">
        <div class="text-[10px] font-bold text-gray-400">已生成用例</div>
        <div class="mt-1 text-lg font-bold text-gray-900">{{ generatedCases }}</div>
      </div>
      <div class="rounded-lg border border-gray-100 bg-gray-50 p-3">
        <div class="text-[10px] font-bold text-gray-400">已执行</div>
        <div class="mt-1 text-lg font-bold text-gray-900">{{ executedCount }}</div>
      </div>
      <div class="rounded-lg border border-emerald-100 bg-emerald-50 p-3">
        <div class="flex items-center gap-1 text-[10px] font-bold text-emerald-600">
          <CheckCircle2 :size="12" />
          通过
        </div>
        <div class="mt-1 text-lg font-bold text-emerald-700">{{ passedCount }}</div>
      </div>
      <div class="rounded-lg border border-red-100 bg-red-50 p-3">
        <div class="flex items-center gap-1 text-[10px] font-bold text-red-500">
          <AlertTriangle :size="12" />
          失败
        </div>
        <div class="mt-1 text-lg font-bold text-red-700">{{ failedCount }}</div>
      </div>
      <div class="rounded-lg border border-amber-100 bg-amber-50 p-3">
        <div class="text-[10px] font-bold text-amber-600">跳过</div>
        <div class="mt-1 text-lg font-bold text-amber-700">{{ skippedCount }}</div>
      </div>
      <div class="rounded-lg border border-blue-100 bg-blue-50 p-3">
        <div class="text-[10px] font-bold text-blue-600">证据</div>
        <div class="mt-1 text-lg font-bold text-blue-700">{{ evidenceCount }}</div>
      </div>
    </div>

    <div class="mt-3 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-xs leading-5 text-gray-600">
      <span class="font-bold text-gray-700">当前阻塞：</span>{{ normalizedBlocker }}
    </div>
  </section>
</template>
