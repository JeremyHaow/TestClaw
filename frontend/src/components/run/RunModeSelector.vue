<script setup lang="ts">
import type { Component, PropType } from 'vue'

type RunMode = {
  value: string
  label: string
  desc: string
  icon: Component
}

defineProps({
  modelValue: {
    type: String,
    required: true,
  },
  modes: {
    type: Array as PropType<RunMode[]>,
    required: true,
  },
})

const emit = defineEmits<{
  (event: 'update:modelValue', mode: string): void
}>()
</script>

<template>
  <div>
    <label class="mb-2 block text-xs font-bold uppercase tracking-widest text-gray-400">测试模式</label>
    <div class="grid gap-3 md:grid-cols-2">
      <button
        v-for="mode in modes"
        :key="mode.value"
        class="min-w-0 rounded-lg border p-4 text-left transition-all"
        :class="modelValue === mode.value ? 'border-blue-500 bg-blue-50 text-blue-800' : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300'"
        @click="emit('update:modelValue', mode.value)"
      >
        <div class="mb-1 flex items-center gap-2">
          <component :is="mode.icon" :size="16" />
          <span class="text-sm font-bold">{{ mode.label }}</span>
        </div>
        <p class="text-xs leading-5 text-gray-500">{{ mode.desc }}</p>
      </button>
    </div>
  </div>
</template>
