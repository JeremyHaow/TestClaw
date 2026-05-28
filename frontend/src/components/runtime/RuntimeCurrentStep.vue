<script setup lang="ts">
import { computed } from 'vue'
import { ArrowRight, Target } from 'lucide-vue-next'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import RuntimeEvaluationPanel from './RuntimeEvaluationPanel.vue'
import RuntimeFailureBadge from './RuntimeFailureBadge.vue'

const props = withDefaults(
  defineProps<{
    action?: Record<string, any> | null
    observation?: Record<string, any> | null
    evaluation?: Record<string, any> | null
  }>(),
  {
    action: null,
    observation: null,
    evaluation: null,
  },
)

const pairedObservation = computed(() => {
  if (!props.action) return props.observation
  const actionId = String(props.action.action_id || '')
  const observationActionId = String(props.observation?.action_id || '')
  if (actionId && observationActionId && actionId === observationActionId) return props.observation
  if (!observationActionId && props.observation?.tool_name === props.action.tool_name) return props.observation
  return null
})
const displayTitle = computed(() => {
  if (props.action?.tool_name) return props.action.tool_name
  if (pairedObservation.value?.tool_name) return pairedObservation.value.tool_name
  if (props.evaluation?.next_action || props.evaluation?.stage) {
    return `Evaluation · ${props.evaluation.next_action || props.evaluation.stage}`
  }
  return 'Waiting for action'
})
const actionInputs = computed(() => props.action?.inputs || pairedObservation.value?.inputs || {})
const expectedObservation = computed(() => props.action?.expected_observation || props.action?.output?.expected_observation || '')
const actualObservation = computed(() => pairedObservation.value?.summary || pairedObservation.value?.observation || '')
</script>

<template>
  <div class="space-y-4">
    <Card class="border-[#E5EAF3] bg-[#FFFFFF] shadow-sm">
      <CardHeader class="pb-3">
        <CardTitle class="flex items-center justify-between gap-3 text-sm text-[#0F172A]">
          <span class="flex min-w-0 items-center gap-2">
            <Target :size="16" class="shrink-0 text-[#2563EB]" />
            <span class="truncate">{{ displayTitle }}</span>
          </span>
          <RuntimeFailureBadge :failure-type="pairedObservation?.failure_type" :status="pairedObservation?.status || action?.status || evaluation?.outcome" />
        </CardTitle>
      </CardHeader>
      <CardContent class="space-y-4">
        <div>
          <div class="text-[11px] font-semibold uppercase text-[#94A3B8]">Why</div>
          <p class="mt-1 text-sm leading-6 text-[#475569]">{{ action?.reason || evaluation?.reason || 'No action reason recorded.' }}</p>
        </div>
        <Separator class="bg-[#EEF2F7]" />
        <div class="grid gap-3 md:grid-cols-2">
          <div class="rounded-md border border-[#EEF2F7] bg-[#F7F9FC] p-3">
            <div class="text-[11px] font-semibold uppercase text-[#94A3B8]">Expected</div>
            <p class="mt-1 text-sm leading-6 text-[#475569]">{{ expectedObservation || 'Expected observation not recorded.' }}</p>
          </div>
          <div class="rounded-md border border-[#EEF2F7] bg-[#F7F9FC] p-3">
            <div class="text-[11px] font-semibold uppercase text-[#94A3B8]">Observed</div>
            <p class="mt-1 text-sm leading-6 text-[#475569]">{{ actualObservation || 'Waiting for observation.' }}</p>
          </div>
        </div>
        <div v-if="Object.keys(actionInputs).length" class="rounded-md border border-[#EEF2F7] bg-[#FFFFFF] p-3">
          <div class="mb-2 text-[11px] font-semibold uppercase text-[#94A3B8]">Tool input</div>
          <pre class="max-h-28 overflow-auto whitespace-pre-wrap text-xs leading-5 text-[#475569]">{{ JSON.stringify(actionInputs, null, 2) }}</pre>
        </div>
        <div class="flex items-center gap-2 text-sm font-semibold text-[#2563EB]">
          <ArrowRight :size="16" />
          <span>{{ evaluation?.next_action || 'waiting' }}</span>
        </div>
      </CardContent>
    </Card>
    <RuntimeEvaluationPanel :evaluation="evaluation" />
  </div>
</template>
