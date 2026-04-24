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
      class="w-full pl-9 pr-8 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-blue-500 focus:bg-white transition-all" />
    <button v-if="local" @click="clear"
      class="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 rounded hover:bg-gray-200 text-gray-400 transition-all">
      <X :size="14" />
    </button>
  </div>
</template>
