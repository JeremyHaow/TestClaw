<script setup lang="ts">
import { computed, ref } from 'vue'
import { Camera, FileText } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import RuntimeFailureBadge from './RuntimeFailureBadge.vue'

const props = withDefaults(
  defineProps<{
    evidence?: Record<string, any>[]
  }>(),
  {
    evidence: () => [],
  },
)

const open = ref(false)
const selectedKind = ref('all')
const kinds = computed(() => ['all', ...Array.from(new Set(props.evidence.map((item) => String(item.kind || 'unknown'))))])
const filteredEvidence = computed(() => {
  if (selectedKind.value === 'all') return props.evidence
  return props.evidence.filter((item) => String(item.kind || 'unknown') === selectedKind.value)
})
</script>

<template>
  <Sheet v-model:open="open">
    <SheetTrigger as-child>
      <Button variant="outline" class="border-[#E5EAF3] text-[#2563EB] hover:bg-[#EFF6FF]">
        <Camera :size="16" />
        Evidence
        <span class="rounded bg-[#EFF6FF] px-1.5 py-0.5 text-xs">{{ evidence.length }}</span>
      </Button>
    </SheetTrigger>
    <SheetContent side="right" class="w-full border-[#E5EAF3] bg-[#FFFFFF] sm:max-w-xl">
      <SheetHeader>
        <SheetTitle class="flex items-center gap-2 text-[#0F172A]">
          <FileText :size="18" class="text-[#2563EB]" />
          Runtime Evidence
        </SheetTitle>
      </SheetHeader>
      <Tabs v-model="selectedKind" class="mt-5">
        <TabsList class="flex h-auto flex-wrap justify-start bg-[#F7F9FC]">
          <TabsTrigger v-for="kind in kinds" :key="kind" :value="kind" class="text-xs">
            {{ kind }}
          </TabsTrigger>
        </TabsList>
        <TabsContent :value="selectedKind" class="mt-4">
          <ScrollArea class="h-[calc(100vh-180px)] pr-3">
            <div class="space-y-3">
              <div
                v-for="item in filteredEvidence"
                :key="item.evidence_id || `${item.kind}-${item.timestamp}`"
                class="rounded-md border border-[#E5EAF3] bg-[#F7F9FC] p-3"
              >
                <div class="flex items-start justify-between gap-3">
                  <div class="min-w-0">
                    <div class="truncate text-sm font-semibold text-[#0F172A]">{{ item.title || item.kind }}</div>
                    <p class="mt-1 text-xs leading-5 text-[#475569]">{{ item.summary || item.kind }}</p>
                  </div>
                  <RuntimeFailureBadge :status="item.status || item.kind" />
                </div>
                <pre v-if="item.data && Object.keys(item.data).length" class="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded border border-[#EEF2F7] bg-[#FFFFFF] p-2 text-xs text-[#475569]">{{ JSON.stringify(item.data, null, 2) }}</pre>
              </div>
              <div v-if="!filteredEvidence.length" class="rounded-md border border-dashed border-[#E5EAF3] bg-[#F7F9FC] p-8 text-center text-sm text-[#94A3B8]">
                No evidence for this filter.
              </div>
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>
    </SheetContent>
  </Sheet>
</template>
