<script setup lang="ts">
import { Inbox } from 'lucide-vue-next'

defineProps<{
  icon?: any
  title?: string
  description?: string
}>()
</script>

<template>
  <div class="flex flex-col items-center justify-center py-16 text-center">
    <div class="p-4 bg-gray-100 rounded-2xl mb-4">
      <component :is="icon || Inbox" :size="32" class="text-gray-400" />
    </div>
    <h3 class="text-sm font-bold text-gray-600 mb-1">{{ title || '暂无数据' }}</h3>
    <p class="text-xs text-gray-400 max-w-[240px]">{{ description || '还没有任何内容' }}</p>
    <slot />
  </div>
</template>
