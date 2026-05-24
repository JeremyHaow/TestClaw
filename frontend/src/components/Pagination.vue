<script setup lang="ts">
import { ChevronLeft, ChevronRight } from 'lucide-vue-next'
import { computed } from 'vue'

const props = defineProps<{
  page: number
  pageSize: number
  total: number
}>()

const emit = defineEmits(['update:page'])

const totalPages = computed(() => Math.ceil(props.total / props.pageSize))
const canPrev = computed(() => props.page > 1)
const canNext = computed(() => props.page < totalPages.value)

const visiblePages = computed(() => {
  const pages: number[] = []
  const start = Math.max(1, props.page - 2)
  const end = Math.min(totalPages.value, props.page + 2)
  for (let i = start; i <= end; i++) pages.push(i)
  return pages
})
</script>

<template>
  <div v-if="totalPages > 1" class="flex flex-col gap-3 rounded-lg border border-gray-200 bg-white px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
    <span class="text-xs font-medium text-gray-500">共 {{ total }} 条，第 {{ page }}/{{ totalPages }} 页</span>
    <div class="flex items-center gap-1">
      <button
        type="button"
        aria-label="上一页"
        title="上一页"
        @click="emit('update:page', page - 1)"
        :disabled="!canPrev"
        class="rounded-lg border border-transparent p-1.5 text-gray-500 transition-all hover:border-gray-200 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-30">
        <ChevronLeft :size="16" />
      </button>
      <button v-for="p in visiblePages" :key="p" @click="emit('update:page', p)"
        class="h-8 w-8 rounded-lg text-xs font-bold transition-all"
        :class="p === page ? 'bg-gray-950 text-white' : 'text-gray-600 hover:bg-gray-100'">
        {{ p }}
      </button>
      <button
        type="button"
        aria-label="下一页"
        title="下一页"
        @click="emit('update:page', page + 1)"
        :disabled="!canNext"
        class="rounded-lg border border-transparent p-1.5 text-gray-500 transition-all hover:border-gray-200 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-30">
        <ChevronRight :size="16" />
      </button>
    </div>
  </div>
</template>
