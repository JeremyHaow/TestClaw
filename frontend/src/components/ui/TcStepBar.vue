<script setup lang="ts">
import { computed } from 'vue'
import { Check, X } from 'lucide-vue-next'

type StepStatus = 'pending' | 'current' | 'complete' | 'error' | 'skipped'
type StepVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

type StepItem = {
  label: string
  description?: string
  status?: StepStatus
}

const props = withDefaults(defineProps<{
  steps: Array<string | StepItem>
  current?: number
  variant?: StepVariant
  clickable?: boolean
}>(), {
  current: 0,
  variant: 'primary',
  clickable: false,
})

const emit = defineEmits<{
  select: [index: number]
}>()

const normalizedSteps = computed<StepItem[]>(() => (
  props.steps.map((step) => (typeof step === 'string' ? { label: step } : step))
))

function resolvedStatus(step: StepItem, index: number): StepStatus {
  if (step.status) return step.status
  if (index < props.current) return 'complete'
  if (index === props.current) return props.variant === 'danger' ? 'error' : 'current'
  return 'pending'
}

function markerClasses(status: StepStatus) {
  if (status === 'complete') return 'border-emerald-500 bg-emerald-500 text-white'
  if (status === 'error') return 'border-red-500 bg-red-500 text-white'
  if (status === 'current') return 'border-blue-600 bg-blue-600 text-white shadow-[0_8px_24px_rgba(37,99,235,0.18)]'
  if (status === 'skipped') return 'border-slate-200 bg-slate-50 text-slate-400'
  return 'border-slate-200 bg-white text-slate-400'
}

function labelClasses(status: StepStatus) {
  if (status === 'complete') return 'text-slate-700'
  if (status === 'error') return 'text-red-700'
  if (status === 'current') return 'text-blue-700'
  return 'text-slate-500'
}

function connectorClasses(index: number) {
  const status = resolvedStatus(normalizedSteps.value[index], index)
  if (status === 'complete') return 'bg-emerald-200'
  if (status === 'error') return 'bg-red-200'
  if (status === 'current') return 'bg-blue-200'
  return props.variant === 'ghost' ? 'bg-transparent' : 'bg-slate-200'
}

function selectStep(index: number) {
  if (props.clickable) emit('select', index)
}
</script>

<template>
  <ol class="flex flex-col gap-3 sm:flex-row sm:items-start">
    <li
      v-for="(step, index) in normalizedSteps"
      :key="`${step.label}-${index}`"
      class="relative flex min-w-0 flex-1 items-start gap-3"
    >
      <div
        v-if="index < normalizedSteps.length - 1"
        class="absolute left-4 top-8 h-[calc(100%-1rem)] w-px sm:left-[calc(2rem+0.375rem)] sm:top-4 sm:h-px sm:w-[calc(100%-2.25rem)]"
        :class="connectorClasses(index)"
      />
      <button
        type="button"
        class="relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-xs font-bold transition"
        :class="[markerClasses(resolvedStatus(step, index)), clickable ? 'hover:scale-105' : 'cursor-default']"
        :disabled="!clickable"
        :aria-current="resolvedStatus(step, index) === 'current' ? 'step' : undefined"
        @click="selectStep(index)"
      >
        <Check v-if="resolvedStatus(step, index) === 'complete'" :size="15" />
        <X v-else-if="resolvedStatus(step, index) === 'error'" :size="15" />
        <span v-else>{{ index + 1 }}</span>
      </button>
      <div class="relative z-10 min-w-0 bg-transparent pr-2">
        <div class="truncate text-sm font-semibold" :class="labelClasses(resolvedStatus(step, index))">
          <slot name="label" :step="step" :index="index" :status="resolvedStatus(step, index)">
            {{ step.label }}
          </slot>
        </div>
        <div v-if="step.description || $slots.description" class="mt-1 text-xs leading-5 text-slate-500">
          <slot name="description" :step="step" :index="index" :status="resolvedStatus(step, index)">
            {{ step.description }}
          </slot>
        </div>
      </div>
    </li>
  </ol>
</template>
