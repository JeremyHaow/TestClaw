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
  <div v-if="totalPages > 1" class="flex items-center justify-between px-2 py-3">
    <span class="text-xs text-gray-400">共 {{ total }} 条，第 {{ page }}/{{ totalPages }} 页</span>
    <div class="flex items-center gap-1">
      <button
        type="button"
        aria-label="上一页"
        title="上一页"
        @click="emit('update:page', page - 1)"
        :disabled="!canPrev"
        class="p-1.5 rounded-lg hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition-all">
        <ChevronLeft :size="16" />
      </button>
      <button v-for="p in visiblePages" :key="p" @click="emit('update:page', p)"
        class="w-8 h-8 rounded-lg text-xs font-bold transition-all"
        :class="p === page ? 'bg-blue-600 text-white' : 'hover:bg-gray-100 text-gray-600'">
        {{ p }}
      </button>
      <button
        type="button"
        aria-label="下一页"
        title="下一页"
        @click="emit('update:page', page + 1)"
        :disabled="!canNext"
        class="p-1.5 rounded-lg hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition-all">
        <ChevronRight :size="16" />
      </button>
    </div>
  </div>
</template>
