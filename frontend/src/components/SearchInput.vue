<script setup lang="ts">
import { Search, X } from 'lucide-vue-next'
import { ref, watch } from 'vue'

const props = defineProps<{ modelValue: string; placeholder?: string }>()
const emit = defineEmits(['update:modelValue'])

const local = ref(props.modelValue)
let timer: any = null

watch(local, (v) => {
  clearTimeout(timer)
  timer = setTimeout(() => emit('update:modelValue', v), 300)
})
watch(() => props.modelValue, (v) => { local.value = v })

function clear() { local.value = ''; emit('update:modelValue', '') }
</script>

<template>
  <div class="relative">
    <Search :size="16" class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
    <input v-model="local" :placeholder="placeholder || '搜索...'"
      class="w-full rounded-lg border border-gray-200 bg-white py-2.5 pl-9 pr-9 text-sm outline-none transition-all placeholder:text-gray-400 focus:border-gray-400 focus:bg-white focus:ring-2 focus:ring-gray-100" />
    <button
      v-if="local"
      type="button"
      aria-label="清空搜索"
      @click="clear"
      class="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg p-1 text-gray-400 transition-all hover:bg-gray-100 hover:text-gray-700"
    >
      <X :size="14" />
    </button>
  </div>
</template>
