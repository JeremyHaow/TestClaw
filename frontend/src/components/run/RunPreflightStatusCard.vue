<script setup lang="ts">
import type { PropType } from 'vue'
import { AlertTriangle, CheckCircle2, Loader2 } from 'lucide-vue-next'

type PreflightCheck = {
  key: string
  label: string
  status: string
  detail: string
  action?: string | null
}

type PreflightStatus = {
  checks: PreflightCheck[]
  warnings: string[]
}

defineProps({
  preflight: {
    type: Object as PropType<PreflightStatus | null>,
    default: null,
  },
  preflightLoading: {
    type: Boolean,
    required: true,
  },
  readiness: {
    type: String,
    required: true,
  },
  readinessLabel: {
    type: String,
    required: true,
  },
  readinessTone: {
    type: Function as PropType<(status: string) => string>,
    required: true,
  },
  checkTone: {
    type: Function as PropType<(status: string) => string>,
    required: true,
  },
})
</script>

<template>
  <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
    <div class="mb-4 flex items-center justify-between gap-3">
      <div>
        <h3 class="text-sm font-bold text-gray-900">预检状态</h3>
        <p class="mt-1 text-xs text-gray-500">运行前确认输入、模型、Worker、浏览器执行器和环境。</p>
      </div>
      <span class="rounded-lg border px-2.5 py-1 text-[10px] font-bold" :class="readinessTone(readiness)">
        {{ readinessLabel }}
      </span>
    </div>

    <div v-if="preflightLoading" class="flex items-center justify-center rounded-lg border border-dashed border-gray-200 bg-gray-50 px-4 py-10 text-sm text-gray-500">
      <Loader2 :size="18" class="mr-2 animate-spin" /> 正在检查工作区...
    </div>

    <div v-else class="space-y-3">
      <div
        v-for="check in preflight?.checks || []"
        :key="check.key"
        class="rounded-lg border px-3 py-3"
        :class="checkTone(check.status)"
      >
        <div class="flex items-start gap-2">
          <CheckCircle2 v-if="check.status === 'ready'" :size="15" class="mt-0.5 shrink-0" />
          <AlertTriangle v-else :size="15" class="mt-0.5 shrink-0" />
          <div class="min-w-0 flex-1">
            <div class="text-xs font-bold">{{ check.label }}</div>
            <div class="mt-0.5 text-xs leading-5 opacity-90">{{ check.detail }}</div>
            <div v-if="check.action" class="mt-1 text-[11px] font-bold opacity-80">{{ check.action }}</div>
          </div>
        </div>
      </div>

      <div v-if="!preflight" class="rounded-lg border border-dashed border-gray-200 bg-gray-50 px-4 py-8 text-center text-sm text-gray-400">
        输入目标后先运行预检，智能体会展示计划路径和缺失配置。
      </div>
    </div>

    <div v-if="preflight?.warnings?.length" class="mt-4 space-y-2">
      <div
        v-for="warning in preflight.warnings"
        :key="warning"
        class="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800"
      >
        <AlertTriangle :size="14" class="mt-0.5 shrink-0" /> {{ warning }}
      </div>
    </div>
  </div>
</template>
