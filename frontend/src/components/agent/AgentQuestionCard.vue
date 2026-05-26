<script setup lang="ts">
import { computed } from 'vue'
import {
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Clock3,
  Loader2,
} from 'lucide-vue-next'
import type {
  IntakeStep,
  PlannerQuestionChoice,
  PlannerQuestionOption,
} from '../../types/agentPlan'

const props = withDefaults(
  defineProps<{
    currentStep: IntakeStep
    questionGroup: PlannerQuestionOption | null
    status: string
    selectedChoice: PlannerQuestionChoice | null
    supplement: string
    canSkip: boolean
    canContinue: boolean
    sending: boolean
    disabled: boolean
  }>(),
  {
    questionGroup: null,
    selectedChoice: null,
    supplement: '',
    canSkip: false,
    canContinue: false,
    sending: false,
    disabled: false,
  },
)

const emit = defineEmits<{
  (event: 'select', group: PlannerQuestionOption, option: PlannerQuestionChoice): void
  (event: 'update:supplement', value: string): void
  (event: 'skip'): void
  (event: 'defer'): void
  (event: 'continue'): void
}>()

const supplementModel = computed({
  get: () => props.supplement,
  set: (value: string) => emit('update:supplement', value),
})

function choiceTitle(option: PlannerQuestionChoice) {
  return option.title || option.label
}

function choiceDescription(option: PlannerQuestionChoice) {
  return option.description || option.message
}

function choiceKey(group: PlannerQuestionOption, option: PlannerQuestionChoice) {
  return `${group.step || group.question}-${option.field || ''}-${option.value || ''}-${option.label}-${option.message}`
}

function isChoiceSelected(group: PlannerQuestionOption, option: PlannerQuestionChoice) {
  const selected = props.selectedChoice
  return Boolean(selected && choiceKey(group, selected) === choiceKey(group, option))
}

function draftStatusClass(status: string) {
  if (status === '已选择' || status === '已收集' || status === '已生成') {
    return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  }
  if (status === '草稿') return 'border-blue-200 bg-blue-50 text-blue-700'
  if (status === '待补充') return 'border-amber-200 bg-amber-50 text-amber-700'
  if (status === '已跳过') return 'border-blue-200 bg-blue-50 text-blue-700'
  return 'border-gray-200 bg-gray-50 text-gray-500'
}
</script>

<template>
  <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
    <div class="flex items-start justify-between gap-3">
      <div class="min-w-0">
        <div class="flex items-center gap-2 text-xs font-bold text-gray-500">
          <CircleDot :size="14" class="text-gray-700" />
          <span>{{ currentStep.label }}</span>
        </div>
        <div class="mt-1 text-base font-semibold leading-6 text-gray-950">
          {{ questionGroup?.question || '补充测试目标、范围、凭据和约束' }}
        </div>
      </div>
      <span
        class="shrink-0 rounded-full border px-2 py-1 text-[11px] font-bold"
        :class="draftStatusClass(status)"
      >
        {{ status }}
      </span>
    </div>

    <div
      v-if="questionGroup"
      class="mt-4 grid gap-2 md:grid-cols-2"
    >
      <button
        v-for="option in questionGroup.options"
        :key="choiceKey(questionGroup, option)"
        type="button"
        class="min-h-[96px] rounded-lg border bg-white p-3 text-left transition disabled:cursor-not-allowed disabled:opacity-60"
        :class="isChoiceSelected(questionGroup, option) ? 'border-gray-950 ring-2 ring-gray-200' : 'border-gray-200 hover:border-gray-400 hover:bg-gray-50'"
        :disabled="sending || disabled"
        @click="emit('select', questionGroup, option)"
      >
        <div class="flex items-start gap-3">
          <div
            class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border"
            :class="isChoiceSelected(questionGroup, option) ? 'border-gray-950 bg-gray-950 text-white' : 'border-gray-300 bg-white text-transparent'"
          >
            <CheckCircle2 :size="13" />
          </div>
          <div class="min-w-0">
            <div class="text-sm font-semibold leading-5 text-gray-950">
              {{ choiceTitle(option) }}
            </div>
            <div class="mt-1 text-xs leading-5 text-gray-500">
              {{ choiceDescription(option) }}
            </div>
          </div>
        </div>
      </button>
    </div>

    <textarea
      v-model="supplementModel"
      rows="3"
      class="mt-4 min-h-[74px] w-full resize-none rounded-lg border border-gray-200 px-3 py-2 text-sm leading-5 outline-none transition focus:border-gray-500 focus:ring-2 focus:ring-gray-200"
      placeholder="补充 URL、接口文档、凭据说明或成功标准"
      :disabled="sending || disabled"
    />

    <div class="mt-3 flex flex-wrap justify-end gap-2">
      <button
        type="button"
        class="inline-flex items-center justify-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-semibold text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="sending || !canSkip || disabled"
        @click="emit('skip')"
      >
        <ChevronRight :size="15" />
        跳过
      </button>
      <button
        type="button"
        class="inline-flex items-center justify-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm font-semibold text-amber-700 hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="sending || disabled"
        @click="emit('defer')"
      >
        <Clock3 :size="15" />
        稍后补充
      </button>
      <button
        type="button"
        class="inline-flex items-center justify-center gap-1.5 rounded-lg bg-gray-950 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-300"
        :disabled="sending || !canContinue || disabled"
        @click="emit('continue')"
      >
        <Loader2 v-if="sending" :size="15" class="animate-spin" />
        <CheckCircle2 v-else :size="15" />
        继续
      </button>
    </div>
  </div>
</template>
