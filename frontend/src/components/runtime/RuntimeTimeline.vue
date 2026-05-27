<script setup lang="ts">
import { computed } from 'vue'
import { Activity, CheckCircle2, CircleDot, ClipboardCheck, Wrench } from 'lucide-vue-next'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import RuntimeFailureBadge from './RuntimeFailureBadge.vue'

const props = withDefaults(
  defineProps<{
    items?: Record<string, any>[]
    active?: boolean
  }>(),
  {
    items: () => [],
    active: false,
  },
)

const orderedItems = computed(() => props.items.slice().reverse())

function iconFor(item: Record<string, any>) {
  const type = String(item.record_type || item.event_type || '').toLowerCase()
  if (type.includes('evaluation')) return ClipboardCheck
  if (type.includes('tool')) return Wrench
  if (['success', 'passed', 'sufficient'].includes(String(item.status || item.outcome || '').toLowerCase())) return CheckCircle2
  return CircleDot
}

function titleFor(item: Record<string, any>) {
  return item.node || item.title || item.tool_name || item.event_type || 'Runtime event'
}

function detailFor(item: Record<string, any>) {
  return item.detail || item.summary || item.reason || item.replan_hint || ''
}
</script>

<template>
  <Card class="border-[#E5EAF3] bg-[#FFFFFF] shadow-sm">
    <CardHeader class="pb-3">
      <CardTitle class="flex items-center justify-between text-sm text-[#0F172A]">
        <span class="flex items-center gap-2">
          <Activity :size="16" class="text-[#2563EB]" />
          Runtime Timeline
        </span>
        <span class="text-xs font-medium text-[#94A3B8]">{{ items.length }} events</span>
      </CardTitle>
    </CardHeader>
    <Separator class="bg-[#EEF2F7]" />
    <CardContent class="p-0">
      <ScrollArea class="h-[360px]">
        <div v-if="orderedItems.length" class="divide-y divide-[#EEF2F7]">
          <div
            v-for="(item, index) in orderedItems"
            :key="item.observation_id || item.evaluation_id || item.action_id || item.tool_call_id || `${item.event_type}-${index}`"
            class="grid grid-cols-[28px_minmax(0,1fr)] gap-3 px-4 py-3"
          >
            <component :is="iconFor(item)" :size="16" class="mt-0.5 text-[#2563EB]" />
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="truncate text-sm font-semibold text-[#0F172A]">{{ titleFor(item) }}</span>
                <RuntimeFailureBadge :failure-type="item.failure_type" :status="item.status || item.outcome" />
              </div>
              <p v-if="detailFor(item)" class="mt-1 text-xs leading-5 text-[#475569]">{{ detailFor(item) }}</p>
              <p v-if="item.timestamp" class="mt-1 font-mono text-[11px] text-[#94A3B8]">
                {{ new Date(item.timestamp).toLocaleString('zh-CN') }}
              </p>
            </div>
          </div>
        </div>
        <div v-else class="flex h-[240px] items-center justify-center px-6 text-center text-sm text-[#94A3B8]">
          {{ active ? 'Waiting for runtime events' : 'No runtime events yet' }}
        </div>
      </ScrollArea>
    </CardContent>
  </Card>
</template>
