<script setup lang="ts">
import {
  AlertTriangle,
  CheckCircle2,
  FileJson,
  Loader2,
  Play,
  ShieldCheck,
  XCircle,
} from 'lucide-vue-next'
import type { PlanDraftItem } from '../../types/agentPlan'

withDefaults(
  defineProps<{
    draftItems: PlanDraftItem[]
    currentPlan: Record<string, any> | null
    currentPayload: Record<string, any> | null
    scopeItems: string[]
    stepItems: string[]
    safetyItems: string[]
    planReady: boolean
    executeError: string
    rejecting: boolean
    executing: boolean
    showActions: boolean
  }>(),
  {
    currentPlan: null,
    currentPayload: null,
    scopeItems: () => [],
    stepItems: () => [],
    safetyItems: () => [],
    planReady: false,
    executeError: '',
    rejecting: false,
    executing: false,
    showActions: false,
  },
)

const emit = defineEmits<{
  (event: 'reject'): void
  (event: 'execute'): void
}>()

function draftStatusClass(status: string) {
  if (status === '已选择' || status === '已收集' || status === '已生成') {
    return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  }
  if (status === '待补充') return 'border-amber-200 bg-amber-50 text-amber-700'
  if (status === '已跳过') return 'border-blue-200 bg-blue-50 text-blue-700'
  return 'border-gray-200 bg-gray-50 text-gray-500'
}
</script>

<template>
  <aside class="tc-card flex min-h-0 flex-col overflow-hidden">
    <div class="border-b border-gray-100 px-4 py-3">
      <div class="flex items-center gap-2">
        <FileJson :size="18" class="text-gray-700" />
        <h2 class="text-base font-semibold text-gray-950">计划草案</h2>
      </div>
      <p class="mt-1 text-xs text-gray-500">{{ planReady ? '等待确认' : '收集进度' }}</p>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto p-4">
      <div class="space-y-3">
        <div
          v-for="item in draftItems"
          :key="item.id"
          class="rounded-lg border border-gray-200 bg-white p-3"
        >
          <div class="flex items-center justify-between gap-2">
            <div class="text-xs font-bold text-gray-500">{{ item.label }}</div>
            <span
              class="shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-bold"
              :class="draftStatusClass(item.status)"
            >
              {{ item.status }}
            </span>
          </div>
          <div class="mt-1 line-clamp-2 text-xs leading-5 text-gray-700">{{ item.value }}</div>
        </div>
      </div>

      <div v-if="!currentPlan" class="mt-4 rounded-lg border border-dashed border-gray-300 bg-gray-50 p-4 text-sm text-gray-500">
        规划器会把下一份可执行任务放在这里。
      </div>

      <div v-else class="mt-4 space-y-4">
        <div>
          <div class="text-sm font-semibold text-gray-950">{{ currentPlan.title || '测试智能体任务计划' }}</div>
          <div class="mt-1 text-xs leading-5 text-gray-600">{{ currentPlan.summary }}</div>
        </div>

        <div class="grid grid-cols-2 gap-2">
          <div class="rounded-lg border border-gray-200 bg-gray-50 p-2">
            <div class="text-[10px] font-bold uppercase text-gray-400">目标</div>
            <div class="mt-1 truncate text-xs font-semibold text-gray-900">
              {{ currentPlan.target || currentPayload?.source }}
            </div>
          </div>
          <div class="rounded-lg border border-gray-200 bg-gray-50 p-2">
            <div class="text-[10px] font-bold uppercase text-gray-400">模式</div>
            <div class="mt-1 text-xs font-semibold uppercase text-gray-900">
              {{ currentPlan.test_type || currentPayload?.test_type }}
            </div>
          </div>
        </div>

        <div v-if="currentPlan.objective" class="rounded-lg border border-gray-200 bg-white p-3">
          <div class="text-[10px] font-bold uppercase text-gray-400">任务目标</div>
          <div class="mt-1 text-xs leading-5 text-gray-700">{{ currentPlan.objective }}</div>
        </div>

        <div v-if="scopeItems.length" class="space-y-2">
          <div class="text-xs font-bold uppercase text-gray-400">范围</div>
          <div v-for="item in scopeItems" :key="item" class="flex gap-2 text-xs leading-5 text-gray-700">
            <CheckCircle2 :size="14" class="mt-0.5 shrink-0 text-emerald-600" />
            <span>{{ item }}</span>
          </div>
        </div>

        <div v-if="stepItems.length" class="space-y-2">
          <div class="text-xs font-bold uppercase text-gray-400">执行步骤</div>
          <div v-for="(item, index) in stepItems" :key="item" class="flex gap-2 text-xs leading-5 text-gray-700">
            <span class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-gray-900 text-[10px] font-bold text-white">{{ index + 1 }}</span>
            <span>{{ item }}</span>
          </div>
        </div>

        <div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
          <div class="mb-2 flex items-center gap-2 text-xs font-bold uppercase text-gray-400">
            <ShieldCheck :size="14" />
            安全边界
          </div>
          <div class="space-y-1.5">
            <div v-for="item in safetyItems" :key="item" class="text-xs leading-5 text-gray-700">{{ item }}</div>
            <div class="text-xs leading-5 text-gray-700">{{ currentPlan.auth_summary || currentPlan.auth }}</div>
          </div>
        </div>

      </div>
    </div>

    <div v-if="currentPlan && showActions" class="border-t border-gray-100 p-3">
      <div v-if="executeError" class="mb-3 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-700">
        <AlertTriangle :size="15" class="mt-0.5 shrink-0" />
        <span>{{ executeError }}</span>
      </div>
      <div class="grid grid-cols-2 gap-2">
        <button
          type="button"
          class="inline-flex items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
          :disabled="rejecting || executing"
          @click="emit('reject')"
        >
          <Loader2 v-if="rejecting" :size="16" class="animate-spin" />
          <XCircle v-else :size="16" />
          拒绝
        </button>
        <button
          type="button"
          class="inline-flex items-center justify-center gap-2 rounded-lg bg-gray-950 px-3 py-2 text-sm font-semibold text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-300"
          :disabled="!planReady || rejecting || executing"
          @click="emit('execute')"
        >
          <Loader2 v-if="executing" :size="16" class="animate-spin" />
          <Play v-else :size="16" />
          立即执行
        </button>
      </div>
    </div>
  </aside>
</template>
