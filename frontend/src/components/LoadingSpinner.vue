<script setup lang="ts">
defineProps<{
  size?: number
  text?: string
}>()
</script>

<template>
  <div class="flex flex-col items-center justify-center py-14">
    <div class="relative">
      <div
        class="rounded-full border-4 border-gray-200"
        :style="{ width: `${size || 40}px`, height: `${size || 40}px` }"
      ></div>
      <div
        class="absolute left-0 top-0 animate-spin rounded-full border-4 border-gray-900 border-t-transparent"
        :style="{ width: `${size || 40}px`, height: `${size || 40}px` }"
      ></div>
    </div>
    <p v-if="text" class="mt-4 text-sm font-medium text-gray-500">{{ text }}</p>
  </div>
</template>
