<script setup lang="ts">
defineProps<{
  size?: number
  text?: string
}>()
</script>

<template>
  <div class="flex flex-col items-center justify-center py-16">
    <div class="relative">
      <div class="w-10 h-10 border-4 border-gray-200 rounded-full"></div>
      <div class="w-10 h-10 border-4 border-blue-600 rounded-full border-t-transparent animate-spin absolute top-0 left-0"></div>
    </div>
    <p v-if="text" class="text-sm text-gray-500 mt-4">{{ text }}</p>
  </div>
</template>
