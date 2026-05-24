<script setup lang="ts">
import { Inbox } from 'lucide-vue-next'

defineProps<{
  icon?: any
  title?: string
  description?: string
}>()
</script>

<template>
  <div class="flex flex-col items-center justify-center rounded-lg border border-dashed border-gray-200 bg-white/70 px-6 py-14 text-center">
    <div class="mb-4 flex h-12 w-12 items-center justify-center rounded-lg border border-gray-200 bg-gray-50">
      <component :is="icon || Inbox" :size="26" class="text-gray-400" />
    </div>
    <h3 class="mb-1 text-sm font-semibold text-gray-800">{{ title || '暂无数据' }}</h3>
    <p class="max-w-[280px] text-xs leading-5 text-gray-500">{{ description || '还没有任何内容' }}</p>
    <slot />
  </div>
</template>
