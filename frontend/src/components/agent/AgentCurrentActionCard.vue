<script setup lang="ts">
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Loader2,
} from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    status: string
    title: string
    description: string
    currentStep: string
    progressPercent: number
    active?: boolean
    failed?: boolean
    cancellable?: boolean
    showNotice?: boolean
  }>(),
  {
    status: '',
    title: 'Agent Cockpit',
    description: '',
    currentStep: '初始化',
    progressPercent: 0,
    active: false,
    failed: false,
    cancellable: false,
    showNotice: false,
  },
)

const emit = defineEmits<{
  (event: 'cancel'): void
}>()

function shellClass() {
  if (props.failed) return 'bg-red-50/70 border-red-100'
  if (props.active) return 'bg-amber-50/70 border-amber-100'
  return 'bg-gray-50 border-gray-100'
}

function progressClass() {
  if (props.failed) return 'bg-red-500'
  if (props.status === 'succeeded') return 'bg-emerald-500'
  return 'bg-amber-500'
}
</script>

<template>
  <div class="p-4 border-b" :class="shellClass()">
    <div class="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2">
          <Loader2 v-if="active" :size="18" class="text-amber-600 animate-spin" />
          <AlertTriangle v-else-if="failed" :size="18" class="text-red-600" />
          <CheckCircle2 v-else-if="status === 'succeeded'" :size="18" class="text-emerald-600" />
          <Clock v-else :size="18" class="text-gray-500" />
          <h3 class="text-lg font-bold text-gray-900">当前动作 · {{ title }}</h3>
        </div>
        <p class="mt-1 text-sm text-gray-600 line-clamp-2">{{ description }}</p>
      </div>
      <div class="flex flex-col items-start gap-2 lg:items-end">
        <div class="text-[10px] font-bold uppercase tracking-widest text-gray-400">当前步骤</div>
        <div class="max-w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-bold text-gray-700 shadow-sm lg:max-w-md">
          {{ currentStep || '初始化' }}
        </div>
      </div>
    </div>

    <div class="mt-5">
      <div class="mb-2 flex items-center justify-between text-[10px] font-bold uppercase tracking-widest text-gray-400">
        <span>执行进度</span>
        <span>{{ progressPercent }}%</span>
      </div>
      <div class="h-2 overflow-hidden rounded-full bg-white border border-gray-200">
        <div
          class="h-full rounded-full transition-all duration-500"
          :class="progressClass()"
          :style="{ width: `${progressPercent}%` }"
        ></div>
      </div>
    </div>

    <div
      v-if="showNotice && $slots.notice"
      class="mt-4 flex flex-col gap-3 rounded-lg border px-4 py-3 text-xs sm:flex-row sm:items-center sm:justify-between"
      :class="failed ? 'border-red-200 bg-white text-red-700' : status === 'cancelled' ? 'border-gray-200 bg-white text-gray-600' : 'border-amber-200 bg-white text-amber-700'"
    >
      <div class="flex min-w-0 items-start gap-2">
        <Activity v-if="active" :size="15" class="mt-0.5 shrink-0" />
        <AlertTriangle v-else :size="15" class="mt-0.5 shrink-0" />
        <span class="min-w-0">
          <slot name="notice" />
        </span>
      </div>
      <button
        v-if="cancellable"
        type="button"
        class="shrink-0 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-[11px] font-bold text-red-600 transition-all hover:bg-red-100"
        @click="emit('cancel')"
      >
        取消运行
      </button>
    </div>

    <slot />
  </div>
</template>
