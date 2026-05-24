<script setup lang="ts">
import { computed } from 'vue'
import { ChevronLeft, ChevronRight } from 'lucide-vue-next'

const props = defineProps<{
  page: number
  pageSize: number
  total: number
  label?: string
}>()

const emit = defineEmits<{
  'update:page': [page: number]
}>()

const totalPages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
const canPrev = computed(() => props.page > 1)
const canNext = computed(() => props.page < totalPages.value)
const start = computed(() => props.total ? (props.page - 1) * props.pageSize + 1 : 0)
const end = computed(() => Math.min(props.total, props.page * props.pageSize))

function updatePage(nextPage: number) {
  const clamped = Math.min(Math.max(1, nextPage), totalPages.value)
  emit('update:page', clamped)
}
</script>

<template>
  <div v-if="total > pageSize" class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] font-bold text-gray-500 shadow-sm">
    <span class="whitespace-nowrap">
      <span v-if="label">{{ label }} </span>{{ start }}-{{ end }} / {{ total }}
    </span>
    <button
      type="button"
      aria-label="上一页"
      title="上一页"
      :disabled="!canPrev"
      class="rounded-md p-1 text-gray-500 transition-all hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-30"
      @click="updatePage(page - 1)"
    >
      <ChevronLeft :size="14" />
    </button>
    <button
      type="button"
      aria-label="下一页"
      title="下一页"
      :disabled="!canNext"
      class="rounded-md p-1 text-gray-500 transition-all hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-30"
      @click="updatePage(page + 1)"
    >
      <ChevronRight :size="14" />
    </button>
  </div>
</template>
