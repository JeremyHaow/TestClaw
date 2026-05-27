<script setup lang="ts">
import { computed } from 'vue'
import { ClipboardCheck } from 'lucide-vue-next'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import RuntimeFailureBadge from './RuntimeFailureBadge.vue'

const props = withDefaults(
  defineProps<{
    evaluation?: Record<string, any> | null
  }>(),
  {
    evaluation: null,
  },
)

const evidenceLabel = computed(() => props.evaluation?.sufficient_evidence ? 'sufficient' : 'insufficient')
</script>

<template>
  <Card class="border-[#E5EAF3] bg-[#FFFFFF] shadow-sm">
    <CardHeader class="pb-3">
      <CardTitle class="flex items-center gap-2 text-sm text-[#0F172A]">
        <ClipboardCheck :size="16" class="text-[#2563EB]" />
        Evaluation
      </CardTitle>
    </CardHeader>
    <CardContent>
      <Alert class="border-[#E5EAF3] bg-[#F7F9FC]">
        <AlertTitle class="flex flex-wrap items-center gap-2 text-[#0F172A]">
          <RuntimeFailureBadge :failure-type="evaluation?.failure_type" :status="evidenceLabel" />
          <span>{{ evaluation?.next_action || 'waiting' }}</span>
        </AlertTitle>
        <AlertDescription class="mt-2 text-sm leading-6 text-[#475569]">
          {{ evaluation?.reason || 'No runtime evaluation has been recorded yet.' }}
        </AlertDescription>
      </Alert>
      <div v-if="evaluation?.missing_evidence?.length" class="mt-3 space-y-2">
        <div
          v-for="(item, index) in evaluation.missing_evidence.slice(0, 4)"
          :key="`missing-${index}`"
          class="rounded-md border border-[#EEF2F7] bg-[#FFFBEB] px-3 py-2 text-xs text-[#92400E]"
        >
          {{ item }}
        </div>
      </div>
    </CardContent>
  </Card>
</template>
