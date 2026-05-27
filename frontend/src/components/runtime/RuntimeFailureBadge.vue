<script setup lang="ts">
import { computed } from 'vue'
import { Badge } from '@/components/ui/badge'

const props = withDefaults(
  defineProps<{
    failureType?: string | null
    status?: string | null
  }>(),
  {
    failureType: '',
    status: '',
  },
)

const label = computed(() => props.failureType || props.status || 'normal')
const toneClass = computed(() => {
  const value = String(props.failureType || props.status || '').toLowerCase()
  if (['success', 'passed', 'sufficient'].includes(value)) return 'border-[#10B981]/30 bg-[#ECFDF5] text-[#047857]'
  if (['blocked', 'failed', 'auth_failure', 'backend_error', 'ui_setup_failed', 'ui_high_risk_action_blocked'].includes(value)) return 'border-[#EF4444]/30 bg-[#FEF2F2] text-[#B91C1C]'
  if (['timeout', 'network_error', 'navigation_blocked', 'replan_api', 'replan_ui', 'retry_same_action'].includes(value)) return 'border-[#F59E0B]/30 bg-[#FFFBEB] text-[#B45309]'
  return 'border-[#E5EAF3] bg-[#EFF6FF] text-[#2563EB]'
})
</script>

<template>
  <Badge variant="outline" :class="toneClass">
    {{ label }}
  </Badge>
</template>
