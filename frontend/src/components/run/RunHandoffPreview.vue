<script setup lang="ts">
import type { PropType } from 'vue'
import { AlertTriangle, KeyRound, Settings2 } from 'lucide-vue-next'

type MissionCorrectionPrompt = {
  key: string
  label: string
  status: string
  detail: string
  action?: string | null
}

type MissionCounts = {
  endpoint_count?: number | null
  estimated_executable_count?: number | null
  estimated_skipped_count?: number | null
  auth_required_count?: number | null
  flow_step_count: number
  check_count: number
  ready_count: number
  review_count: number
  blocked_count: number
}

type MissionPreview = {
  handoff: string
  readiness: string
  target: string
  input_mode: string
  test_mode: string
  objective: string
  scope: string
  execution_policy: string
  safety_boundary: string
  auth_readiness: string
  counts: MissionCounts
  correction_prompts: MissionCorrectionPrompt[]
}

type CountItem = {
  label: string
  value: string
}

type RunForm = {
  test_type: string
}

defineProps({
  missionPreview: {
    type: Object as PropType<MissionPreview | null>,
    default: null,
  },
  missionCountItems: {
    type: Array as PropType<CountItem[]>,
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
  missionAuthTone: {
    type: String,
    required: true,
  },
  localInputType: {
    type: String,
    required: true,
  },
  form: {
    type: Object as PropType<RunForm>,
    required: true,
  },
  isApiMode: {
    type: Boolean,
    required: true,
  },
  endpointCountLabel: {
    type: String,
    required: true,
  },
  authProvidedTone: {
    type: String,
    required: true,
  },
  authProvidedLabel: {
    type: String,
    required: true,
  },
})
</script>

<template>
  <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
    <div class="mb-4 flex items-center justify-between gap-3">
      <div class="flex items-center gap-2">
        <Settings2 :size="16" class="text-gray-500" />
        <h3 class="text-sm font-bold text-gray-900">任务交接预览</h3>
      </div>
      <span
        v-if="missionPreview"
        class="rounded-lg border px-2.5 py-1 text-[10px] font-bold"
        :class="readinessTone(missionPreview.readiness)"
      >
        {{ readinessLabel }}
      </span>
    </div>

    <div v-if="missionPreview" class="space-y-4">
      <div class="rounded-lg border px-3 py-3 text-xs font-bold" :class="readinessTone(missionPreview.readiness)">
        {{ missionPreview.handoff }}
      </div>

      <div class="space-y-3 text-xs text-gray-600">
        <div class="space-y-1">
          <span class="block text-gray-400">目标</span>
          <span class="block break-words font-mono font-bold text-gray-800">{{ missionPreview.target }}</span>
        </div>
        <div class="flex items-start justify-between gap-3">
          <span class="shrink-0 text-gray-400">推断模式</span>
          <span class="min-w-0 text-right font-bold text-gray-800">{{ missionPreview.input_mode }} / {{ missionPreview.test_mode }}</span>
        </div>
        <div class="space-y-1">
          <span class="block text-gray-400">任务目标</span>
          <span class="block font-bold leading-5 text-gray-800">{{ missionPreview.objective }}</span>
        </div>
        <div class="space-y-1">
          <span class="block text-gray-400">测试范围</span>
          <span class="block leading-5 text-gray-700">{{ missionPreview.scope }}</span>
        </div>
        <div class="space-y-1">
          <span class="block text-gray-400">执行策略</span>
          <span class="block leading-5 text-gray-700">{{ missionPreview.execution_policy }}</span>
        </div>
        <div class="space-y-1">
          <span class="block text-gray-400">安全边界</span>
          <span class="block leading-5 text-gray-700">{{ missionPreview.safety_boundary }}</span>
        </div>
        <div class="space-y-1">
          <span class="block text-gray-400">鉴权准备</span>
          <span class="block leading-5" :class="missionAuthTone">{{ missionPreview.auth_readiness }}</span>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-2">
        <div
          v-for="item in missionCountItems"
          :key="item.label"
          class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2"
        >
          <div class="text-[10px] font-bold text-gray-400">{{ item.label }}</div>
          <div class="mt-0.5 text-sm font-bold text-gray-900">{{ item.value }}</div>
        </div>
      </div>

      <div v-if="missionPreview.correction_prompts.length" class="space-y-2">
        <div class="text-xs font-bold text-gray-900">启动前可修正</div>
        <div
          v-for="prompt in missionPreview.correction_prompts"
          :key="prompt.key"
          class="rounded-lg border px-3 py-2"
          :class="checkTone(prompt.status)"
        >
          <div class="flex items-start gap-2">
            <AlertTriangle :size="14" class="mt-0.5 shrink-0" />
            <div class="min-w-0">
              <div class="text-xs font-bold">{{ prompt.label }}</div>
              <div class="mt-0.5 text-xs leading-5 opacity-90">{{ prompt.detail }}</div>
              <div v-if="prompt.action" class="mt-1 text-[11px] font-bold opacity-80">{{ prompt.action }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="space-y-3 text-xs text-gray-600">
      <div class="rounded-lg border border-dashed border-gray-200 bg-gray-50 px-4 py-6 text-center text-sm text-gray-400">
        运行预检后，这里会展示智能体准备接收的目标、范围、策略和待修正项。
      </div>
      <div class="flex items-start justify-between gap-3">
        <span class="shrink-0 text-gray-400">输入类型</span>
        <span class="min-w-0 text-right font-bold text-gray-800">{{ localInputType }}</span>
      </div>
      <div class="flex items-start justify-between gap-3">
        <span class="shrink-0 text-gray-400">测试模式</span>
        <span class="font-bold uppercase text-gray-800">{{ form.test_type }}</span>
      </div>
      <div v-if="isApiMode" class="flex items-start justify-between gap-3">
        <span class="shrink-0 text-gray-400">API 端点</span>
        <span class="font-bold text-gray-800">{{ endpointCountLabel }}</span>
      </div>
      <div v-if="isApiMode" class="flex items-start justify-between gap-3">
        <span class="shrink-0 text-gray-400">凭据</span>
        <span class="flex items-center gap-1 font-bold" :class="authProvidedTone">
          <KeyRound :size="13" /> {{ authProvidedLabel }}
        </span>
      </div>
    </div>
  </div>
</template>
